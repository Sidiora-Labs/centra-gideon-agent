use hypermid_contracts::Id;
use hypermid_models::{ModelCatalog, ModelRecord};
use hypermid_store::budget::{AdmissionRequest, OwnerBudgetPolicy};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OwnerModelSelection {
    pub provider_id: String,
    pub model_id: String,
    pub region: String,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct ProjectModelConstraints {
    pub max_cost_nanodollars: Option<u64>,
    pub max_output_tokens: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ModelJob {
    pub reservation_id: Id,
    pub owner_id: Id,
    pub project_id: Id,
    pub job_id: Id,
    pub job_class: String,
    pub background: bool,
    pub estimated_input_tokens: u64,
    pub estimated_output_tokens: u64,
    pub now_ms: u64,
}

pub fn prepare_admission(
    policy: &OwnerBudgetPolicy,
    selection: &OwnerModelSelection,
    constraints: ProjectModelConstraints,
    catalog: &ModelCatalog,
    job: ModelJob,
) -> Result<AdmissionRequest, ModelAdmissionError> {
    if job.owner_id != policy.owner_id || job.project_id != policy.project_id {
        return Err(ModelAdmissionError::PolicyDenied);
    }
    let allowed = policy.providers.iter().any(|provider| {
        provider.enabled
            && provider.provider_id == selection.provider_id
            && provider.model_id == selection.model_id
            && provider.region == selection.region
    });
    if !allowed {
        return Err(ModelAdmissionError::PolicyDenied);
    }
    if constraints
        .max_output_tokens
        .is_some_and(|maximum| job.estimated_output_tokens > maximum)
    {
        return Err(ModelAdmissionError::ProjectConstraint);
    }
    let model = catalog
        .providers
        .iter()
        .find(|provider| provider.id == selection.provider_id)
        .and_then(|provider| {
            provider
                .models
                .iter()
                .find(|model| model.id == selection.model_id)
        })
        .ok_or(ModelAdmissionError::CatalogMissing)?;
    let estimated_cost_nanodollars = estimate_cost(model, &job)?;
    if constraints
        .max_cost_nanodollars
        .is_some_and(|maximum| estimated_cost_nanodollars > maximum)
    {
        return Err(ModelAdmissionError::ProjectConstraint);
    }
    Ok(AdmissionRequest {
        reservation_id: job.reservation_id,
        owner_id: job.owner_id,
        project_id: job.project_id,
        job_id: job.job_id,
        job_class: job.job_class,
        provider_id: selection.provider_id.clone(),
        model_id: selection.model_id.clone(),
        region: selection.region.clone(),
        background: job.background,
        estimated_input_tokens: job.estimated_input_tokens,
        estimated_output_tokens: job.estimated_output_tokens,
        estimated_cost_nanodollars,
        now_ms: job.now_ms,
    })
}

fn estimate_cost(model: &ModelRecord, job: &ModelJob) -> Result<u64, ModelAdmissionError> {
    let input = model
        .input_price
        .as_ref()
        .ok_or(ModelAdmissionError::PriceMissing)?
        .nanodollars_per_million_tokens;
    let output = model
        .output_price
        .as_ref()
        .ok_or(ModelAdmissionError::PriceMissing)?
        .nanodollars_per_million_tokens;
    let input_cost = scaled_cost(job.estimated_input_tokens, input)?;
    let output_cost = scaled_cost(job.estimated_output_tokens, output)?;
    input_cost
        .checked_add(output_cost)
        .ok_or(ModelAdmissionError::PriceOverflow)
}

fn scaled_cost(tokens: u64, rate: u64) -> Result<u64, ModelAdmissionError> {
    let numerator = u128::from(tokens)
        .checked_mul(u128::from(rate))
        .ok_or(ModelAdmissionError::PriceOverflow)?;
    let rounded_up = numerator
        .checked_add(999_999)
        .ok_or(ModelAdmissionError::PriceOverflow)?
        / 1_000_000;
    u64::try_from(rounded_up).map_err(|_| ModelAdmissionError::PriceOverflow)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum ModelAdmissionError {
    #[error("owner model policy denied the request")]
    PolicyDenied,
    #[error("project constraint denied the request")]
    ProjectConstraint,
    #[error("selected model is absent from the catalog")]
    CatalogMissing,
    #[error("selected model price is missing")]
    PriceMissing,
    #[error("model price calculation overflowed")]
    PriceOverflow,
}

#[cfg(test)]
mod tests {
    use super::*;
    use hypermid_models::{ModelRecord, PriceRate, ProviderRecord};
    use hypermid_store::budget::{BudgetLimits, ProviderPolicy};
    use std::collections::BTreeMap;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    #[test]
    fn owner_selection_is_exact_and_project_constraints_only_tighten() {
        let policy = OwnerBudgetPolicy {
            owner_id: id("owner-1"),
            project_id: id("project-1"),
            revision: 1,
            limits: BudgetLimits {
                max_call_nanodollars: 100,
                max_concurrent: 1,
                max_hourly_nanodollars: 100,
                max_daily_nanodollars: 100,
                max_job_nanodollars: 100,
            },
            providers: vec![ProviderPolicy {
                provider_id: "centra".into(),
                model_id: "model-a".into(),
                region: "eu".into(),
                enabled: true,
            }],
        };
        let catalog = ModelCatalog {
            providers: vec![ProviderRecord {
                id: "centra".into(),
                models: vec![ModelRecord {
                    id: "model-a".into(),
                    modalities: vec![],
                    context_tokens: Some(1_000),
                    input_price: Some(PriceRate {
                        nanodollars_per_million_tokens: 1_000_000,
                    }),
                    output_price: Some(PriceRate {
                        nanodollars_per_million_tokens: 2_000_000,
                    }),
                    raw: BTreeMap::new(),
                }],
                raw: BTreeMap::new(),
            }],
        };
        let job = ModelJob {
            reservation_id: id("reservation-1"),
            owner_id: id("owner-1"),
            project_id: id("project-1"),
            job_id: id("job-1"),
            job_class: "summary".into(),
            background: true,
            estimated_input_tokens: 10,
            estimated_output_tokens: 20,
            now_ms: 1,
        };
        let selection = OwnerModelSelection {
            provider_id: "centra".into(),
            model_id: "model-a".into(),
            region: "eu".into(),
        };
        let request = prepare_admission(
            &policy,
            &selection,
            ProjectModelConstraints {
                max_cost_nanodollars: Some(50),
                max_output_tokens: Some(20),
            },
            &catalog,
            job.clone(),
        )
        .unwrap();
        assert_eq!(request.estimated_cost_nanodollars, 50);

        let mut forbidden = selection;
        forbidden.region = "us".into();
        assert_eq!(
            prepare_admission(
                &policy,
                &forbidden,
                ProjectModelConstraints::default(),
                &catalog,
                job,
            ),
            Err(ModelAdmissionError::PolicyDenied)
        );
    }
}
