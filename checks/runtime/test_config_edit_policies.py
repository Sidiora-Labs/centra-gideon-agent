import pytest

from gideon.core.config.edit_spec import ConfigValueError, coerce_edit_value


@pytest.mark.parametrize(
    "spec,value,expected",
    [
        ({"type": "int", "min": 0, "max": 4}, "4", 4),
        ({"type": "float"}, "0.5", 0.5),
        ({"type": "bool"}, False, False),
        ({"type": "duration"}, " 12h ", "12h"),
        ({"type": "enum", "values": ["off", "on"]}, "off", "off"),
        ({"type": "str", "values": ["on"]}, "on", "on"),
        ({"type": "str_list", "each_regex": True}, ["^x$"], ["^x$"]),
        (
            {"type": "https_url"},
            " https://notify.example/topic ",
            "https://notify.example/topic",
        ),
        ({"type": "https_url"}, " ", ""),
    ],
)
def test_write_policies_keep_accepted_values(spec, value, expected):
    assert coerce_edit_value("field", value, spec) == expected


@pytest.mark.parametrize(
    "spec,value",
    [
        ({"type": "int"}, True),
        ({"type": "float"}, float("nan")),
        ({"type": "bool"}, "false"),
        ({"type": "duration"}, "0m"),
        ({"type": "str_list", "each_regex": True}, ["["]),
        ({"type": "https_url"}, "http://notify.example/topic"),
        ({"type": "egress"}, {"allow_hosts": ["https://example.com"]}),
        ({"type": "projection_rules"}, [{"match_regex": "[", "strategy": "log"}]),
        ({"type": "skill_catalogs"}, [{"url": "file:///private"}]),
    ],
)
def test_invalid_edits_expose_a_rejection_resource(spec, value):
    with pytest.raises(ConfigValueError) as raised:
        coerce_edit_value("field", value, spec)
    assert raised.value.status == 400
    assert raised.value.resources.startswith("field")


def test_record_policies_normalize_fields_and_remove_unknown_keys():
    rule = coerce_edit_value(
        "tools.projection_rules",
        [
            {
                "name": " tail ",
                "match_regex": " .* ",
                "strategy": " LOG ",
                "head": "2",
                "tail": 0,
                "keep": " error ",
                "unknown": "discard",
            }
        ],
        {"type": "projection_rules"},
    )
    assert rule == [
        {
            "name": "tail",
            "match_regex": ".*",
            "strategy": "log",
            "head": 2,
            "keep": "error",
        }
    ]
    catalogs = coerce_edit_value(
        "packs.catalogs",
        [
            {
                "name": " Team ",
                "url": " https://example.com/index ",
                "kind": " TAP ",
                "unknown": 1,
            }
        ],
        {"type": "skill_catalogs"},
    )
    assert catalogs == [
        {"name": "Team", "url": "https://example.com/index", "kind": "tap"}
    ]
    assert coerce_edit_value("security.egress", {"unknown": 1}, {"type": "egress"}) == {
        "allow_hosts": [],
        "deny_hosts": [],
        "allow_private": False,
    }


def test_unknown_policy_is_an_internal_configuration_error():
    with pytest.raises(ConfigValueError) as raised:
        coerce_edit_value("field", "value", {"type": "missing"})
    assert raised.value.status == 500
