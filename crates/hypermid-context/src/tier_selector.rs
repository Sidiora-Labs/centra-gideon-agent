use hypermid_contracts::Digest;
use hypermid_core::decay::{
    select_tiers, selection_input_digest, DecayError, TierSelection, TierSelectionRequest,
};
use std::collections::BTreeMap;
use std::sync::Mutex;

#[derive(Clone, Debug, Eq, PartialEq)]
struct LockedSelection {
    input_digest: Digest,
    selection: TierSelection,
}

#[derive(Debug, Default)]
pub struct TierSelector {
    generations: Mutex<BTreeMap<u64, LockedSelection>>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TierSelectorError {
    Decay(DecayError),
    GenerationLocked,
}

impl TierSelector {
    pub fn select(
        &self,
        request: &TierSelectionRequest,
    ) -> Result<TierSelection, TierSelectorError> {
        let input_digest = selection_input_digest(request).map_err(TierSelectorError::Decay)?;
        let mut generations = self
            .generations
            .lock()
            .expect("tier selector lock poisoned");
        if let Some(locked) = generations.get(&request.generation) {
            if locked.input_digest != input_digest {
                return Err(TierSelectorError::GenerationLocked);
            }
            return Ok(locked.selection.clone());
        }
        let selection = select_tiers(request).map_err(TierSelectorError::Decay)?;
        generations.insert(
            request.generation,
            LockedSelection {
                input_digest,
                selection: selection.clone(),
            },
        );
        Ok(selection)
    }
}
