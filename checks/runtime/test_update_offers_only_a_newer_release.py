from gideon.operations import self_update as su

ORDERING = [
    ("0.3.0-rc.1", "0.3.0-rc.2"),
    ("0.3.0-rc.2", "0.3.0"),
    ("0.3.0-beta.2", "0.3.0-rc.1"),
    ("0.3.0-alpha.1", "0.3.0-beta.1"),
    ("0.3.0rc1", "v0.3.0-rc.2"),
    ("0.2.9", "0.3.0-rc.1"),
    ("0.1.9", "0.1.10"),
]

MOVES = [
    ("v0.2.1", "0.2.0", "", True),
    ("v0.1.3", "0.2.0", "", False),
    ("v0.2.0", "0.2.0", "", False),
    ("v0.1.3", "0.2.0", "0.1.3", True),
    ("v0.3.0-rc.1", "0.3.0rc1", "0.3.0-rc.1", False),
    ("", "0.2.0", "", False),
    ("", "0.2.0", "0.1.3", False),
]


def assert_update_offer_vectors() -> None:
    for older, newer in ORDERING:
        assert su.is_newer(newer, older)
        assert not su.is_newer(older, newer)
        assert not su.same_version(older, newer)
        assert (
            su.UpdateStatus("pip", older, {"tag": newer}, None).wire()[
                "update_available"
            ]
            is True
        )
        assert (
            su.UpdateStatus("pip", newer, {"tag": older}, None).wire()[
                "update_available"
            ]
            is False
        )
    for target, running, pin, moves in MOVES:
        assert su.moves_to(target, running, pin) is moves
        if not pin:
            status = su.UpdateStatus("container", running, {"tag": target}, None).wire()
            assert status["update_available"] is moves
            assert bool(status["instructions"]) is moves


def test_channels_only_move_forward_and_pins_allow_explicit_rollback() -> None:
    assert_update_offer_vectors()
    assert not su.moves_to("v0.1.2", "0.2.0", "0.1.3")
