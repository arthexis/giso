from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

import pytest

from giso import AnsibleExecutionError, AnsibleInspectionError, Giso
import giso.ansible as ansible_module


COLLECTION = "community.general"
NMCLI = "community.general.nmcli"
INVENTORY = "./inventory.yml"

MODULE_LIST = {
    NMCLI: "Manage networking with nmcli",
    "community.general.systemd_creds": "Manage systemd credentials",
    "other.collection.ignore_me": "Not part of the selected collection",
}

MODULE_DOCS = {
    NMCLI: {
        "doc": {
            "short_description": "Manage networking with nmcli",
            "description": ["Create and modify NetworkManager connections."],
            "options": {
                "conn_name": {"type": "str", "required": True, "aliases": ["name"]},
                "state": {
                    "type": "str",
                    "default": "present",
                    "choices": ["present", "absent"],
                },
                "autoconnect": {"type": "bool"},
                "_internal": {"type": "str"},
                "not-valid": {"type": "str"},
            },
        }
    },
    "community.general.systemd_creds": {
        "doc": {
            "short_description": "Manage systemd credentials",
            "options": {},
        }
    },
}


class AnsibleDocHarness:
    def __init__(self, monkeypatch, *, listing=None, docs=None):
        self.listing = MODULE_LIST if listing is None else listing
        self.docs = MODULE_DOCS if docs is None else docs

        def run(cls, *args):
            if "-l" in args:
                return self.listing
            requested = [arg for arg in args if arg.startswith(f"{COLLECTION}.")]
            return {name: self.docs[name] for name in requested if name in self.docs}

        monkeypatch.setattr(Giso, "_run_ansible_doc", classmethod(run))


@dataclass
class AnsibleRunHarness:
    monkeypatch: Any
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    commands: list[list[str]] = field(default_factory=list)
    write_results: bool = True

    def __post_init__(self):
        self.monkeypatch.setattr(
            ansible_module.shutil,
            "which",
            lambda executable: f"/usr/bin/{executable}",
        )

        harness = self

        class Completed:
            def __init__(self):
                self.returncode = harness.returncode
                self.stdout = harness.stdout
                self.stderr = harness.stderr

        def run(command, **kwargs):
            harness.commands.append(command)
            if harness.write_results:
                tree = command[command.index("--tree") + 1]
                for host, result in harness.results.items():
                    with open(
                        ansible_module.os.path.join(tree, host),
                        "w",
                        encoding="utf-8",
                    ) as handle:
                        ansible_module.json.dump(result, handle)
            return Completed()

        self.monkeypatch.setattr(ansible_module.subprocess, "run", run)

    @property
    def command(self) -> list[str]:
        return self.commands[-1]


def nmcli_operation(monkeypatch, **ansible_kwargs):
    AnsibleDocHarness(monkeypatch)
    giso = Giso().ansible(COLLECTION, **ansible_kwargs)
    return giso, giso.community.general.modules.nmcli


def nmcli_request(*, host="localhost", inventory="localhost,", connection="local"):
    return ansible_module.AnsibleModuleRequest(
        fqcn=NMCLI,
        args={"conn_name": "eth0"},
        context=ansible_module.AnsibleExecutionContext(
            host=host,
            inventory=inventory,
            connection=connection,
        ),
    )


def test_ansible_collection_exposes_inspection_operations(monkeypatch):
    _, operation = nmcli_operation(monkeypatch)

    assert operation.__doc__ == "Manage networking with nmcli"
    assert operation.ansible_spec.fqcn == NMCLI
    assert operation.ansible_spec.description == (
        "Create and modify NetworkManager connections.",
    )
    assert tuple(operation.ansible_spec.options) == (
        "conn_name",
        "state",
        "autoconnect",
    )


def test_ansible_module_signature_comes_from_documented_options(monkeypatch):
    AnsibleDocHarness(monkeypatch)
    operation = Giso(f"ansible:{COLLECTION}").community.general.modules.nmcli
    signature = inspect.signature(operation)

    assert tuple(signature.parameters) == ("conn_name", "state", "autoconnect")
    assert signature.parameters["conn_name"].default is inspect.Parameter.empty
    assert signature.parameters["conn_name"].annotation is str
    assert signature.parameters["state"].default == "present"
    assert signature.parameters["autoconnect"].default is None
    assert signature.parameters["autoconnect"].annotation is bool


def test_ansible_inspection_never_executes_module(monkeypatch):
    giso, operation = nmcli_operation(monkeypatch)

    with pytest.raises(AnsibleInspectionError, match="inspection-only"):
        operation(conn_name="eth0")

    assert not giso.results.history


def test_ansible_collection_records_provenance(monkeypatch):
    giso, _ = nmcli_operation(monkeypatch)

    assert giso.provenance == [
        {
            "type": "ansible-collection",
            "source": f"ansible:{COLLECTION}",
            "collection": COLLECTION,
            "modules": "community.general.nmcli,community.general.systemd_creds",
        }
    ]


def test_ansible_collection_can_be_mounted_in_named_branch(monkeypatch):
    AnsibleDocHarness(monkeypatch)
    giso = Giso(automation=f"ansible:{COLLECTION}")

    assert giso.automation.community.general.modules.nmcli.ansible_spec.name == "nmcli"
    assert giso.provenance[0]["source"] == f"ansible:{COLLECTION}"


def test_ansible_collection_is_atomic_when_module_docs_are_missing(monkeypatch):
    AnsibleDocHarness(
        monkeypatch,
        docs={NMCLI: MODULE_DOCS[NMCLI]},
    )
    giso = Giso()

    with pytest.raises(AnsibleInspectionError, match="no metadata"):
        giso.ansible(COLLECTION)

    assert not giso.operations
    assert not giso.provenance


@pytest.mark.parametrize(
    "collection",
    ["", "community", "community.general.extra", "_private.general", "bad-name.general"],
)
def test_ansible_collection_name_is_validated(collection):
    error = TypeError if collection == "" else ValueError
    with pytest.raises(error):
        Giso._validate_collection_name(collection)


def test_ansible_collection_requires_modules(monkeypatch):
    AnsibleDocHarness(monkeypatch, listing={})

    with pytest.raises(AnsibleInspectionError, match="exposes no modules"):
        Giso().ansible(COLLECTION)


def test_ansible_doc_is_optional_until_resolver_is_used(monkeypatch):
    monkeypatch.setattr(ansible_module.shutil, "which", lambda executable: None)

    with pytest.raises(AnsibleInspectionError, match="requires ansible-doc"):
        Giso._run_ansible_doc("-t", "module", "-l", "-j", COLLECTION)


def test_ansible_prepare_returns_validated_request(monkeypatch):
    giso, operation = nmcli_operation(monkeypatch)

    request = operation.prepare(
        conn_name="eth0",
        state="present",
        autoconnect=True,
    )

    assert request.fqcn == NMCLI
    assert request.args == {
        "conn_name": "eth0",
        "state": "present",
        "autoconnect": True,
    }
    assert request.context == ansible_module.AnsibleExecutionContext(
        host="localhost",
        inventory="localhost,",
        connection="local",
    )
    assert not giso.results.history


def test_ansible_prepare_normalizes_aliases(monkeypatch):
    _, operation = nmcli_operation(monkeypatch)

    request = operation.prepare(name="eth0")

    assert request.args == {"conn_name": "eth0"}


def test_ansible_prepare_does_not_inject_documented_defaults(monkeypatch):
    _, operation = nmcli_operation(monkeypatch)

    request = operation.prepare(conn_name="eth0")

    assert request.args == {"conn_name": "eth0"}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "missing required"),
        ({"conn_name": "eth0", "unknown": 1}, "no documented option"),
        ({"conn_name": "eth0", "name": "eth1"}, "supplied more than once"),
        ({"conn_name": 3}, "expects str"),
        ({"conn_name": "eth0", "autoconnect": "yes"}, "expects bool"),
        ({"conn_name": "eth0", "state": "invalid"}, "must be one of"),
    ],
)
def test_ansible_prepare_rejects_invalid_requests(monkeypatch, kwargs, message):
    _, operation = nmcli_operation(monkeypatch)

    with pytest.raises(AnsibleInspectionError, match=message):
        operation.prepare(**kwargs)


def test_ansible_prepare_works_under_named_branch(monkeypatch):
    AnsibleDocHarness(monkeypatch)
    giso = Giso(automation=f"ansible:{COLLECTION}")

    request = giso.automation.community.general.modules.nmcli.prepare(name="eth0")

    assert request.fqcn == NMCLI
    assert request.args == {"conn_name": "eth0"}
    assert not giso.results.history


def test_ansible_execute_runs_prepared_request_on_localhost(monkeypatch):
    giso, operation = nmcli_operation(monkeypatch)
    AnsibleRunHarness(
        monkeypatch,
        results={"localhost": {"changed": False, "msg": "ok"}},
    )

    result = giso.execute(operation.prepare(name="eth0"))

    assert result == {"changed": False, "msg": "ok"}
    assert giso.results.last == result
    assert giso.results.history[-1][0] == f"ansible.localhost.{NMCLI}"


def test_ansible_execute_requires_prepared_request():
    with pytest.raises(TypeError, match="AnsibleModuleRequest"):
        Giso().execute({"fqcn": NMCLI})


def test_ansible_execute_requires_ansible_binary(monkeypatch):
    monkeypatch.setattr(ansible_module.shutil, "which", lambda executable: None)

    with pytest.raises(AnsibleExecutionError, match="requires ansible"):
        Giso().execute(nmcli_request())


def test_ansible_execute_surfaces_module_failure(monkeypatch):
    AnsibleRunHarness(
        monkeypatch,
        results={"localhost": {"failed": True, "msg": "device missing"}},
        returncode=2,
    )

    with pytest.raises(AnsibleExecutionError, match="device missing") as captured:
        Giso().execute(nmcli_request())

    assert captured.value.result == {"failed": True, "msg": "device missing"}


def test_ansible_execute_rejects_missing_result_file(monkeypatch):
    AnsibleRunHarness(
        monkeypatch,
        returncode=1,
        stdout="broken output",
        write_results=False,
    )

    with pytest.raises(AnsibleExecutionError, match="no valid result"):
        Giso().execute(nmcli_request())


def test_ansible_module_args_are_serialized_deterministically():
    rendered = Giso._serialize_module_args(
        {
            "name": "wired connection",
            "enabled": True,
            "count": 2,
            "items": ["a", "b"],
            "settings": {"mode": "auto"},
        }
    )

    assert rendered == (
        "name='wired connection' enabled=true count=2 "
        "items='[\"a\",\"b\"]' settings='{\"mode\":\"auto\"}'"
    )


def test_ansible_prepare_carries_bound_inventory_context(monkeypatch):
    _, operation = nmcli_operation(
        monkeypatch,
        inventory=INVENTORY,
        host="gway-004",
    )

    request = operation.prepare(name="eth0")

    assert request.context == ansible_module.AnsibleExecutionContext(
        host="gway-004",
        inventory=INVENTORY,
        connection=None,
    )


def test_ansible_execute_uses_bound_inventory_and_host(monkeypatch):
    giso, operation = nmcli_operation(
        monkeypatch,
        inventory=INVENTORY,
        host="gway-004",
        connection="ssh",
    )
    runner = AnsibleRunHarness(
        monkeypatch,
        results={"gway-004": {"changed": True}},
    )

    result = giso.execute(operation.prepare(name="eth0"))

    assert result == {"changed": True}
    assert runner.command[1] == "gway-004"
    assert runner.command[runner.command.index("--inventory") + 1] == INVENTORY
    assert runner.command[runner.command.index("--connection") + 1] == "ssh"
    assert giso.results.history[-1][0] == f"ansible.gway-004.{NMCLI}"


def test_ansible_execute_uses_inventory_connection_when_unbound(monkeypatch):
    giso, operation = nmcli_operation(
        monkeypatch,
        inventory=INVENTORY,
        host="gway-004",
    )
    runner = AnsibleRunHarness(
        monkeypatch,
        results={"gway-004": {"changed": False}},
    )

    giso.execute(operation.prepare(name="eth0"))

    assert "--connection" not in runner.command


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"host": "gway-004"}, "inventory is required"),
        ({"inventory": INVENTORY}, "host is required"),
        ({"inventory": "", "host": "gway-004"}, "inventory must be"),
        ({"inventory": INVENTORY, "host": "bad host"}, "must not contain whitespace"),
        (
            {"inventory": INVENTORY, "host": "gway-004", "connection": ""},
            "connection must be",
        ),
        ({"connection": "ssh"}, "implicit localhost inventory only supports local"),
    ],
)
def test_ansible_execution_context_is_validated(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Giso._execution_context(
            inventory=kwargs.get("inventory"),
            host=kwargs.get("host"),
            connection=kwargs.get("connection"),
        )


def test_ansible_group_execution_returns_results_by_host(monkeypatch):
    giso, operation = nmcli_operation(
        monkeypatch,
        inventory=INVENTORY,
        host="chargers",
    )
    runner = AnsibleRunHarness(
        monkeypatch,
        results={
            "gway-004": {"changed": True},
            "gway-005": {"changed": False},
        },
    )

    result = giso.execute(operation.prepare(name="eth0"))

    assert result == {
        "gway-004": {"changed": True},
        "gway-005": {"changed": False},
    }
    assert runner.command[1] == "chargers"
    assert giso.results[f"ansible.gway-004.{NMCLI}"] == {"changed": True}
    assert giso.results[f"ansible.gway-005.{NMCLI}"] == {"changed": False}
    assert giso.results[f"ansible.chargers.{NMCLI}"] == result


def test_ansible_group_execution_preserves_partial_failures(monkeypatch):
    giso, operation = nmcli_operation(
        monkeypatch,
        inventory=INVENTORY,
        host="chargers",
    )
    AnsibleRunHarness(
        monkeypatch,
        results={
            "gway-004": {"changed": True},
            "gway-005": {"failed": True, "msg": "boom"},
        },
        returncode=2,
    )

    with pytest.raises(AnsibleExecutionError, match="gway-005") as captured:
        giso.execute(operation.prepare(name="eth0"))

    assert captured.value.result == {
        "gway-004": {"changed": True},
        "gway-005": {"failed": True, "msg": "boom"},
    }
    assert giso.results[f"ansible.gway-004.{NMCLI}"] == {"changed": True}
    assert giso.results[f"ansible.gway-005.{NMCLI}"]["failed"] is True
    assert giso.results[f"ansible.chargers.{NMCLI}"] == captured.value.result


def test_ansible_group_execution_treats_unreachable_as_failure(monkeypatch):
    AnsibleRunHarness(
        monkeypatch,
        results={
            "gway-004": {"unreachable": True, "msg": "ssh failed"},
            "gway-005": {"changed": False},
        },
        returncode=4,
    )

    with pytest.raises(AnsibleExecutionError, match="gway-004") as captured:
        Giso().execute(
            nmcli_request(
                host="chargers",
                inventory=INVENTORY,
                connection=None,
            )
        )

    assert captured.value.result["gway-004"]["unreachable"] is True
