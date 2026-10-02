pub use hypermid_core::provider::{
    serialize_for_host, HostSerializedProjection, ProviderProfile, ProviderSerializationError,
};

#[derive(Clone, Copy, Debug, Default)]
pub struct HostSerializer;

impl HostSerializer {
    pub fn serialize(
        &self,
        projection: &hypermid_core::projection::Projection,
        profile: &ProviderProfile,
        previous: Option<&hypermid_core::provider::ProviderGeneration>,
    ) -> Result<HostSerializedProjection, ProviderSerializationError> {
        serialize_for_host(projection, profile, previous)
    }
}
