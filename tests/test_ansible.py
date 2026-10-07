from __future__ import annotations

import inspect

import pytest

from giso import AnsibleExecutionError, AnsibleInspectionError, Giso
import giso.ansible as ansible_module


MODULE_LIST = {
    "community.general.nmcli": "Manage networking with nmcli",
    "community.general.systemd_creds": "Manage systemd credentials",
    "other.collection.ignore_me": "Not part of the selected collection",
}

MODULE_DOCS = {
    "community.general.nmcli": {
        "doc": {
            "short_description": "Manage networking with nmcli",
            "description": ["Create and modify NetworkManager connections."],
            "options": {
                "conn_name": {"type": "str", "required": True, "aliases": ["name"]},
                "state": {"type": "str", "default": "present", "choices": ["present", "absent"]},
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


def patch_ansible_doc(monkeypatch, *, listing=None, docs=None):
    listing = MODULE_LIST if listing is None else listing
    docs = MODULE_DOCS if docs is None else docs

    def run(cls, *args):
        if "-l" in args:
            return listing
        requested = [arg for arg in args if arg.startswith("community.general.")]
        return {name: docs[name] for name in requested if name in docs}

    monkeypatch.setattr(Giso, "_run_ansible_doc", classmethod(run))


def test_ansible_collection_exposes_inspection_operations(monkeypatch):
    patch_ansible_doc(monkeypatch)

    giso = Giso().ansible("community.general")

    operation = giso.community.general.modules.nmcli
    assert operation.__doc__ == "Manage networking with nmcli"
    assert operation.ansible_spec.fqcn == "community.general.nmcli"
    assert operation.ansible_spec.description == (
        "Create and modify NetworkManager connections.",
    )
    assert tuple(operation.ansible_spec.options) == (
        "conn_name",
        "state",
        "autoconnect",
    )


def test_ansible_module_signature_comes_from_documented_options(monkeypatch):
    patch_ansible_doc(monkeypatch)

    operation = Giso("ansible:community.general").community.general.modules.nmcli
    signature = inspect.signature(operation)

    assert tuple(signature.parameters) == ("conn_name", "state", "autoconnect")
    assert signature.parameters["conn_name"].default is inspect.Parameter.empty
    assert signature.parameters["conn_name"].annotation is str
    assert signature.parameters["state"].default == "present"
    assert signature.parameters["autoconnect"].default is None
    assert signature.parameters["autoconnect"].annotation is bool


def test_ansible_inspection_never_executes_module(monkeypatch):
    patch_ansible_doc(monkeypatch)
    giso = Giso().ansible("community.general")

    with pytest.raises(AnsibleInspectionError, match="inspection-only"):
        giso.community.general.modules.nmcli(conn_name="eth0")

    assert not giso.results.history


def test_ansible_collection_records_provenance(monkeypatch):
    patch_ansible_doc(monkeypatch)

    giso = Giso().ansible("community.general")

    assert giso.provenance == [
        {
            "type": "ansible-collection",
            "source": "ansible:community.general",
            "collection": "community.general",
            "modules": "community.general.nmcli,community.general.systemd_creds",
        }
    ]


def test_ansible_collection_can_be_mounted_in_named_branch(monkeypatch):
    patch_ansible_doc(monkeypatch)

    giso = Giso(automation="ansible:community.general")

    assert giso.automation.community.general.modules.nmcli.ansible_spec.name == "nmcli"
    assert giso.provenance[0]["source"] == "ansible:community.general"


def test_ansible_collection_is_atomic_when_module_docs_are_missing(monkeypatch):
    patch_ansible_doc(
        monkeypatch,
        docs={"community.general.nmcli": MODULE_DOCS["community.general.nmcli"]},
    )
    giso = Giso()

    with pytest.raises(AnsibleInspectionError, match="no metadata"):
        giso.ansible("community.general")

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
    patch_ansible_doc(monkeypatch, listing={})

    with pytest.raises(AnsibleInspectionError, match="exposes no modules"):
        Giso().ansible("community.general")


def test_ansible_doc_is_optional_until_resolver_is_used(monkeypatch):
    monkeypatch.setattr(ansible_module.shutil, "which", lambda executable: None)

    with pytest.raises(AnsibleInspectionError, match="requires ansible-doc"):
        Giso._run_ansible_doc("-t", "module", "-l", "-j", "community.general")


def test_ansible_prepare_returns_validated_request(monkeypatch):
    patch_ansible_doc(monkeypatch)
    operation = Giso().ansible("community.general").community.general.modules.nmcli

    request = operation.prepare(conn_name="eth0", state="present", autoconnect=True)

    assert request.fqcn == "community.general.nmcli"
    assert request.args == {
        "conn_name": "eth0",
        "state": "present",
        "autoconnect": True,
    }
    assert not Giso().results.history


def test_ansible_prepare_normalizes_aliases(monkeypatch):
    patch_ansible_doc(monkeypatch)
    operation = Giso().ansible("community.general").community.general.modules.nmcli

    request = operation.prepare(name="eth0")

    assert request.args == {"conn_name": "eth0"}


def test_ansible_prepare_does_not_inject_documented_defaults(monkeypatch):
    patch_ansible_doc(monkeypatch)
    operation = Giso().ansible("community.general").community.general.modules.nmcli

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
    patch_ansible_doc(monkeypatch)
    operation = Giso().ansible("community.general").community.general.modules.nmcli

    with pytest.raises(AnsibleInspectionError, match=message):
        operation.prepare(**kwargs)


def test_ansible_prepare_works_under_named_branch(monkeypatch):
    patch_ansible_doc(monkeypatch)
    giso = Giso(automation="ansible:community.general")

    request = giso.automation.community.general.modules.nmcli.prepare(name="eth0")

    assert request.fqcn == "community.general.nmcli"
    assert request.args == {"conn_name": "eth0"}
    assert not giso.results.history


def patch_local_execution(monkeypatch, tmp_path, *, result, returncode=0, stdout="", stderr=""):
    monkeypatch.setattr(ansible_module.shutil, "which", lambda executable: f"/usr/bin/{executable}")

    class Completed:
        def __init__(self):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def run(command, **kwargs):
        tree = command[command.index("--tree") + 1]
        result_path = ansible_module.os.path.join(tree, "localhost")
        with open(result_path, "w", encoding="utf-8") as handle:
            ansible_module.json.dump(result, handle)
        return Completed()

    monkeypatch.setattr(ansible_module.subprocess, "run", run)


def test_ansible_execute_runs_prepared_request_on_localhost(monkeypatch, tmp_path):
    patch_ansible_doc(monkeypatch)
    patch_local_execution(
        monkeypatch,
        tmp_path,
        result={"changed": False, "msg": "ok"},
    )
    giso = Giso().ansible("community.general")
    request = giso.community.general.modules.nmcli.prepare(name="eth0")

    result = giso.execute(request)

    assert result == {"changed": False, "msg": "ok"}
    assert giso.results.last == result
    assert giso.results.history[-1][0] == "ansible.localhost.community.general.nmcli"


def test_ansible_execute_requires_prepared_request():
    with pytest.raises(TypeError, match="AnsibleModuleRequest"):
        Giso().execute({"fqcn": "community.general.nmcli"})


def test_ansible_execute_requires_ansible_binary(monkeypatch):
    monkeypatch.setattr(ansible_module.shutil, "which", lambda executable: None)
    request = ansible_module.AnsibleModuleRequest(
        fqcn="community.general.nmcli",
        args={"conn_name": "eth0"},
    )

    with pytest.raises(AnsibleExecutionError, match="requires ansible"):
        Giso().execute(request)


def test_ansible_execute_surfaces_module_failure(monkeypatch, tmp_path):
    patch_local_execution(
        monkeypatch,
        tmp_path,
        result={"failed": True, "msg": "device missing"},
        returncode=2,
    )
    request = ansible_module.AnsibleModuleRequest(
        fqcn="community.general.nmcli",
        args={"conn_name": "eth0"},
    )

    with pytest.raises(AnsibleExecutionError, match="device missing") as captured:
        Giso().execute(request)

    assert captured.value.result == {"failed": True, "msg": "device missing"}
    assert not Giso().results.history


def test_ansible_execute_rejects_missing_result_file(monkeypatch):
    monkeypatch.setattr(ansible_module.shutil, "which", lambda executable: "/usr/bin/ansible")

    class Completed:
        returncode = 1
        stdout = "broken output"
        stderr = ""

    monkeypatch.setattr(ansible_module.subprocess, "run", lambda *args, **kwargs: Completed())
    request = ansible_module.AnsibleModuleRequest(
        fqcn="community.general.nmcli",
        args={"conn_name": "eth0"},
    )

    with pytest.raises(AnsibleExecutionError, match="no valid result"):
        Giso().execute(request)


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
        "items='[\"a\",\"b\"]' settings='{"mode":"auto"}'"
    )
