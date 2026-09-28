from gideon.automation.triggers.models import validate_outcome_route
from gideon.integrations.channel_transports.reference_echo import ReferenceEchoTransport


def test_named_provider_target_is_parsed_and_validated_by_its_transport():
    destination = "channel:reference-echo:room-a"
    assert validate_outcome_route(destination) == []
    provider, _, target = destination[len("channel:"):].partition(":")
    transport = ReferenceEchoTransport()
    assert provider == transport.name
    assert transport.validate_target(target) == ""
    assert transport.validate_target("room a")
    assert validate_outcome_route("channel:reference-echo:room a")
