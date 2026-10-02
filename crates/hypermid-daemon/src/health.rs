use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
    sync::{Arc, RwLock},
};

use hypermid_contracts::{Error, Id};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug)]
pub struct DurableStore {
    path: PathBuf,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DurableStoreHealth {
    pub storage_version: u64,
    pub writable: bool,
    pub digest_intact: bool,
}

impl DurableStore {
    pub fn bootstrap(path: impl AsRef<Path>) -> Result<Self, String> {
        let path = path.as_ref().to_path_buf();
        let store = hypermid_memory::MemoryStore::open(&path).map_err(|error| error.to_string())?;
        let evidence = store.schema_evidence().map_err(|error| error.to_string())?;
        if evidence.current_version != hypermid_memory::MEMORY_SCHEMA_VERSION
            || evidence.schema_digest != hypermid_memory::migrations::schema_digest()
        {
            return Err("durable memory schema evidence does not match this build".into());
        }
        drop(store);
        let store = Self { path };
        store.probe()?;
        Ok(store)
    }

    pub fn probe(&self) -> Result<DurableStoreHealth, String> {
        use rusqlite::{Connection, OpenFlags};

        let connection = Connection::open_with_flags(
            &self.path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|error| error.to_string())?;
        connection
            .execute_batch("BEGIN IMMEDIATE; ROLLBACK;")
            .map_err(|error| error.to_string())?;
        let integrity: String = connection
            .query_row("PRAGMA quick_check", [], |row| row.get(0))
            .map_err(|error| error.to_string())?;
        let (storage_version, schema_digest): (u64, String) = connection
            .query_row(
                "SELECT current_version, schema_digest FROM hypermid_schema_version WHERE singleton=1",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .map_err(|error| error.to_string())?;
        let migration_state: String = connection
            .query_row(
                "SELECT state FROM hypermid_migration_journal WHERE version=?1",
                [storage_version],
                |row| row.get(0),
            )
            .map_err(|error| error.to_string())?;
        let migration_digest: String = connection
            .query_row(
                "SELECT digest FROM hypermid_migrations WHERE version=?1",
                [storage_version],
                |row| row.get(0),
            )
            .map_err(|error| error.to_string())?;
        let migrations = hypermid_memory::migrations::memory_migrations();
        let expected = migrations
            .iter()
            .find(|migration| migration.version == storage_version)
            .ok_or_else(|| format!("unsupported durable schema version {storage_version}"))?;
        let digest_intact = integrity == "ok"
            && storage_version == hypermid_memory::MEMORY_SCHEMA_VERSION
            && schema_digest == hypermid_memory::migrations::schema_digest().to_hex()
            && migration_state == "finished"
            && migration_digest == expected.digest();
        Ok(DurableStoreHealth {
            storage_version,
            writable: true,
            digest_intact,
        })
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum Evidence<T> {
    Available {
        observed_at_ms: u64,
        value: T,
    },
    Unavailable {
        observed_at_ms: u64,
        reason: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        last_good_ms: Option<u64>,
    },
    Unsupported {
        reason: String,
    },
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ModuleHealth {
    pub module_id: Id,
    pub process: Evidence<String>,
    pub registration: Evidence<String>,
    pub readiness: Evidence<bool>,
    pub probe: Evidence<Value>,
    pub resources: Evidence<Value>,
    pub route_breaker: Evidence<String>,
    pub dependencies: Evidence<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_error: Option<Error>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HealthSnapshot {
    pub daemon_instance_id: Id,
    pub registry_generation: u64,
    pub observed_at_ms: u64,
    pub status: String,
    pub daemon: BTreeMap<String, Evidence<Value>>,
    pub modules: Vec<ModuleHealth>,
}

#[derive(Clone, Default)]
pub struct HealthTracker {
    daemon: Arc<RwLock<BTreeMap<String, Evidence<Value>>>>,
    modules: Arc<RwLock<BTreeMap<Id, ModuleHealth>>>,
}

impl HealthTracker {
    pub fn set_daemon_evidence(&self, name: impl Into<String>, evidence: Evidence<Value>) {
        if let Ok(mut daemon) = self.daemon.write() {
            daemon.insert(name.into(), evidence);
        }
    }

    pub fn set_module(&self, health: ModuleHealth) {
        if let Ok(mut modules) = self.modules.write() {
            modules.insert(health.module_id.clone(), health);
        }
    }

    pub fn remove_module(&self, module_id: &Id) {
        if let Ok(mut modules) = self.modules.write() {
            modules.remove(module_id);
        }
    }

    pub fn snapshot(
        &self,
        daemon_instance_id: Id,
        registry_generation: u64,
        observed_at_ms: u64,
    ) -> HealthSnapshot {
        let daemon = self
            .daemon
            .read()
            .map(|values| values.clone())
            .unwrap_or_else(|_| {
                BTreeMap::from([(
                    "health_store".into(),
                    Evidence::Unavailable {
                        observed_at_ms,
                        reason: "health state lock is unavailable".into(),
                        last_good_ms: None,
                    },
                )])
            });
        let modules: Vec<_> = self
            .modules
            .read()
            .map(|values| values.values().cloned().collect())
            .unwrap_or_default();
        let status = aggregate_status(&daemon, &modules).into();
        HealthSnapshot {
            daemon_instance_id,
            registry_generation,
            observed_at_ms,
            status,
            daemon,
            modules,
        }
    }
}

fn aggregate_status(
    daemon: &BTreeMap<String, Evidence<Value>>,
    modules: &[ModuleHealth],
) -> &'static str {
    let daemon_unavailable = daemon
        .values()
        .any(|evidence| matches!(evidence, Evidence::Unavailable { .. }));
    let module_failing = modules.iter().any(|module| {
        matches!(module.readiness, Evidence::Available { value: false, .. })
            || module.last_error.is_some()
    });
    let daemon_failing = daemon.values().any(|evidence| {
        matches!(evidence, Evidence::Available { value: Value::Bool(false), .. })
            || matches!(evidence, Evidence::Available { value: Value::String(value), .. } if value == "broken")
    });
    if module_failing || daemon_failing {
        "failing"
    } else if daemon_unavailable
        || daemon
            .values()
            .any(|evidence| matches!(evidence, Evidence::Unsupported { .. }))
    {
        "degraded"
    } else if daemon.is_empty() && modules.is_empty() {
        "unknown"
    } else {
        "healthy"
    }
}
