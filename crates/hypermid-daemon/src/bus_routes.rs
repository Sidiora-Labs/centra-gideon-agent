use std::collections::BTreeMap;
use std::path::Path;
use std::sync::{Arc, Mutex};

use hypermid_bus::{DurableEventBus, EventBusError, EventDraft};
use hypermid_contracts::{Cursor, Id, Scope};
use hypermid_protocol::Principal;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::sync::broadcast;

pub const BUS_OPERATIONS: [&str; 4] = [
    "events.publish",
    "events.subscribe",
    "events.ack",
    "events.unsubscribe",
];

#[derive(Clone)]
pub struct BusRoutes {
    inner: Arc<Mutex<BusRouteState>>,
    changed: broadcast::Sender<()>,
    max_active_subscriptions: usize,
}

struct BusRouteState {
    bus: DurableEventBus,
    active: BTreeMap<Id, SubscriptionLease>,
}

#[derive(Clone)]
struct SubscriptionLease {
    session_id: Id,
    scope: Scope,
    delivery_in_flight: bool,
}

#[derive(Debug, thiserror::Error)]
pub enum BusRouteError {
    #[error("bus route state is unavailable")]
    Poisoned,
    #[error("bus request payload is invalid: {0}")]
    InvalidPayload(String),
    #[error("subscription is owned by another authenticated session")]
    SubscriptionOwnerMismatch,
    #[error("active subscription capacity is exhausted")]
    Backpressure,
    #[error("unsupported bus operation")]
    UnsupportedOperation,
    #[error(transparent)]
    Bus(#[from] EventBusError),
}

impl BusRouteError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::Poisoned => "BUS_UNAVAILABLE",
            Self::InvalidPayload(_) => "INVALID_REQUEST",
            Self::SubscriptionOwnerMismatch => "SCOPE_DENIED",
            Self::Backpressure => "BACKPRESSURE",
            Self::UnsupportedOperation => "UNKNOWN_OPERATION",
            Self::Bus(EventBusError::ScopeDenied(_)) => "SCOPE_DENIED",
            Self::Bus(EventBusError::DivergentDuplicate) => "DIVERGENT_DUPLICATE",
            Self::Bus(EventBusError::CursorGap { .. }) => "CURSOR_GAP",
            Self::Bus(EventBusError::ConsumerResumeMismatch) => "CONSUMER_RESUME_MISMATCH",
            Self::Bus(EventBusError::UnknownConsumer) => "UNKNOWN_CONSUMER",
            Self::Bus(EventBusError::InvalidAcknowledgement) => "INVALID_ACKNOWLEDGEMENT",
            Self::Bus(EventBusError::InvalidTopic) => "INVALID_TOPIC",
            Self::Bus(_) => "BUS_FAILURE",
        }
    }

    pub fn recovery_cursor(&self) -> Option<Cursor> {
        match self {
            Self::Bus(EventBusError::CursorGap { recovery }) => Some(*recovery),
            _ => None,
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SubscribeRequest {
    consumer_id: Id,
    scope: Scope,
    topic_filter: String,
    #[serde(default)]
    after: Option<Cursor>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AcknowledgeRequest {
    consumer_id: Id,
    event_id: Id,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct UnsubscribeRequest {
    consumer_id: Id,
}

impl BusRoutes {
    pub fn open(
        path: impl AsRef<Path>,
        epoch: u64,
        max_retained: usize,
        max_deliveries: u32,
        max_active_subscriptions: usize,
    ) -> Result<Self, BusRouteError> {
        if max_active_subscriptions == 0 {
            return Err(BusRouteError::Backpressure);
        }
        let bus = DurableEventBus::open(path, epoch, max_retained, max_deliveries)?;
        let (changed, _) = broadcast::channel(1);
        Ok(Self {
            inner: Arc::new(Mutex::new(BusRouteState {
                bus,
                active: BTreeMap::new(),
            })),
            changed,
            max_active_subscriptions,
        })
    }

    pub fn subscribe_changes(&self) -> broadcast::Receiver<()> {
        self.changed.subscribe()
    }

    pub fn execute(
        &self,
        session_id: &Id,
        principal: &Principal,
        bound_scope: &Scope,
        operation: &str,
        payload: Option<Value>,
    ) -> Result<Value, BusRouteError> {
        match operation {
            "events.publish" => self.publish(principal, bound_scope, payload),
            "events.subscribe" => self.subscribe(session_id, principal, bound_scope, payload),
            "events.ack" => self.acknowledge(session_id, bound_scope, payload),
            "events.unsubscribe" => self.unsubscribe(session_id, bound_scope, payload),
            _ => Err(BusRouteError::UnsupportedOperation),
        }
    }

    pub fn next_event(
        &self,
        session_id: &Id,
        consumer_id: &Id,
        now_ms: u64,
    ) -> Result<Option<Value>, BusRouteError> {
        let mut state = self.inner.lock().map_err(|_| BusRouteError::Poisoned)?;
        let lease = state
            .active
            .get(consumer_id)
            .ok_or(EventBusError::UnknownConsumer)?;
        if &lease.session_id != session_id {
            return Err(BusRouteError::SubscriptionOwnerMismatch);
        }
        if lease.delivery_in_flight {
            return Ok(None);
        }
        let Some(delivery) = state.bus.next_delivery(consumer_id, now_ms)? else {
            return Ok(None);
        };
        state
            .active
            .get_mut(consumer_id)
            .expect("subscription lease remains active while the route lock is held")
            .delivery_in_flight = true;
        let mut event = serde_json::to_value(delivery.event)
            .map_err(|error| BusRouteError::InvalidPayload(error.to_string()))?;
        event
            .as_object_mut()
            .expect("EventRecord serializes to an object")
            .insert("delivery_count".into(), json!(delivery.delivery_count));
        Ok(Some(json!({
            "subscription_id": consumer_id,
            "event": event,
        })))
    }

    pub fn subscription_ids(&self, session_id: &Id) -> Result<Vec<Id>, BusRouteError> {
        let state = self.inner.lock().map_err(|_| BusRouteError::Poisoned)?;
        Ok(state
            .active
            .iter()
            .filter(|(_, lease)| &lease.session_id == session_id)
            .map(|(consumer_id, _)| consumer_id.clone())
            .collect())
    }

    pub fn close_session(&self, session_id: &Id) -> Result<(), BusRouteError> {
        let mut state = self.inner.lock().map_err(|_| BusRouteError::Poisoned)?;
        state
            .active
            .retain(|_, lease| &lease.session_id != session_id);
        Ok(())
    }

    pub fn cancel_subscription(
        &self,
        session_id: &Id,
        consumer_id: &Id,
    ) -> Result<bool, BusRouteError> {
        let mut state = self.inner.lock().map_err(|_| BusRouteError::Poisoned)?;
        let Some(lease) = state.active.get(consumer_id) else {
            return Ok(false);
        };
        if &lease.session_id != session_id {
            return Err(BusRouteError::SubscriptionOwnerMismatch);
        }
        state.active.remove(consumer_id);
        Ok(true)
    }

    fn publish(
        &self,
        principal: &Principal,
        bound_scope: &Scope,
        payload: Option<Value>,
    ) -> Result<Value, BusRouteError> {
        let draft: EventDraft = decode(payload)?;
        if &draft.scope != bound_scope {
            return Err(BusRouteError::SubscriptionOwnerMismatch);
        }
        let mut state = self.inner.lock().map_err(|_| BusRouteError::Poisoned)?;
        let event = state.bus.publish(principal, draft)?;
        drop(state);
        let _ = self.changed.send(());
        serde_json::to_value(event)
            .map_err(|error| BusRouteError::InvalidPayload(error.to_string()))
    }

    fn subscribe(
        &self,
        session_id: &Id,
        principal: &Principal,
        bound_scope: &Scope,
        payload: Option<Value>,
    ) -> Result<Value, BusRouteError> {
        let request: SubscribeRequest = decode(payload)?;
        if &request.scope != bound_scope {
            return Err(BusRouteError::SubscriptionOwnerMismatch);
        }
        let mut state = self.inner.lock().map_err(|_| BusRouteError::Poisoned)?;
        if let Some(lease) = state.active.get(&request.consumer_id) {
            if &lease.session_id != session_id || lease.scope != request.scope {
                return Err(BusRouteError::SubscriptionOwnerMismatch);
            }
        } else if state.active.len() >= self.max_active_subscriptions {
            return Err(BusRouteError::Backpressure);
        }
        let snapshot = state.bus.subscribe(
            principal,
            request.consumer_id.clone(),
            request.scope.clone(),
            request.topic_filter,
            request.after,
        )?;
        state.active.insert(
            request.consumer_id.clone(),
            SubscriptionLease {
                session_id: session_id.clone(),
                scope: request.scope,
                delivery_in_flight: false,
            },
        );
        drop(state);
        let _ = self.changed.send(());
        Ok(json!({
            "subscription_id": request.consumer_id,
            "cursor": snapshot.cursor,
            "replay": [],
        }))
    }

    fn acknowledge(
        &self,
        session_id: &Id,
        bound_scope: &Scope,
        payload: Option<Value>,
    ) -> Result<Value, BusRouteError> {
        let request: AcknowledgeRequest = decode(payload)?;
        let mut state = self.inner.lock().map_err(|_| BusRouteError::Poisoned)?;
        let lease = state
            .active
            .get(&request.consumer_id)
            .ok_or(EventBusError::UnknownConsumer)?;
        if &lease.session_id != session_id || &lease.scope != bound_scope {
            return Err(BusRouteError::SubscriptionOwnerMismatch);
        }
        state
            .bus
            .acknowledge(&request.consumer_id, &request.event_id)?;
        state
            .active
            .get_mut(&request.consumer_id)
            .expect("subscription lease remains active while the route lock is held")
            .delivery_in_flight = false;
        drop(state);
        let _ = self.changed.send(());
        Ok(json!({"acknowledged": true}))
    }

    fn unsubscribe(
        &self,
        session_id: &Id,
        bound_scope: &Scope,
        payload: Option<Value>,
    ) -> Result<Value, BusRouteError> {
        let request: UnsubscribeRequest = decode(payload)?;
        let mut state = self.inner.lock().map_err(|_| BusRouteError::Poisoned)?;
        let lease = state
            .active
            .get(&request.consumer_id)
            .ok_or(EventBusError::UnknownConsumer)?;
        if &lease.session_id != session_id || &lease.scope != bound_scope {
            return Err(BusRouteError::SubscriptionOwnerMismatch);
        }
        state.active.remove(&request.consumer_id);
        Ok(json!({"unsubscribed": true}))
    }
}

fn decode<T: for<'de> Deserialize<'de>>(payload: Option<Value>) -> Result<T, BusRouteError> {
    serde_json::from_value(payload.unwrap_or(Value::Null))
        .map_err(|error| BusRouteError::InvalidPayload(error.to_string()))
}
