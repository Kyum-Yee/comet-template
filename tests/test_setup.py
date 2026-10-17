"""Offline tests for config and setup. Run: python3 tests/test_setup.py  (or pytest)."""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from comet_tpl import config, setup  # noqa: E402


def _tmp_config():
    d = tempfile.mkdtemp(prefix="comet-cfg-")
    config.CONFIG_DIR, config.CONFIG_FILE = d, os.path.join(d, "config.json")
    return d


def test_config_keeps_only_changes_and_survives_a_broken_file():
    # fails if defaults leak into the file (later default changes would be masked) or a bad file crashes every command
    d = _tmp_config()
    config.set_key("works_dir", "~/작품 폴더")
    config.add_material("자료.md", "캐릭터 설계 전")
    config.add_material("자료.md", "이름 짓기 전")
    saved = open(config.CONFIG_FILE, encoding="utf-8").read()
    assert "username" not in saved and config.get("works_dir") == os.path.expanduser("~/작품 폴더")
    assert [m["when"] for m in config.get("materials")] == ["이름 짓기 전"]
    open(config.CONFIG_FILE, "w").write("{broken")
    assert config.load()["username"] == "유저"
    try:
        config.set_key("nope", "1")
        raise AssertionError("unknown key accepted")
    except KeyError:
        pass
    shutil.rmtree(d)


def test_shim_runs_from_a_path_with_spaces_quotes_and_korean():
    # fails if the sh shim's quoting breaks on paths like "~/내 폴더/it's"
    if os.name == "nt":
        return
    base = tempfile.mkdtemp(prefix="comet-shim-")
    skill = os.path.join(base, "내 폴더", "it's")
    os.makedirs(skill)
    with open(os.path.join(skill, "comet.py"), "w") as f:
        f.write("import sys; print('ARGS', sys.argv[1:])\n")
    env = dict(os.environ, PATH=os.path.dirname(sys.executable) + os.pathsep + "/usr/bin:/bin")
    path, _ = setup.install_shim(skill, os.path.join(base, "bin"))
    out = subprocess.run([path, "a b", "c"], capture_output=True, text=True, env=env)
    assert "ARGS ['a b', 'c']" in out.stdout, out.stderr
    shutil.rmtree(base)


def test_install_skill_moves_the_old_copy_aside_and_leaves_a_foreign_folder():
    # fails if an update deletes local edits to the previous install, or wipes an unrelated folder sharing the name
    base = tempfile.mkdtemp(prefix="comet-skills-")
    setup.BACKUP_DIR = os.path.join(base, "_backup")
    dst, backup = setup.install_skill(base)
    assert backup is None and os.path.exists(os.path.join(dst, "comet_tpl", "setup.py"))
    assert not os.path.exists(os.path.join(dst, ".git"))
    open(os.path.join(dst, "my-edit.md"), "w").write("mine")
    dst2, backup = setup.install_skill(base)
    assert dst2 == dst and not os.path.exists(os.path.join(dst, "my-edit.md"))
    assert open(os.path.join(backup, "my-edit.md")).read() == "mine"
    foreign = os.path.join(tempfile.mkdtemp(prefix="comet-foreign-"), setup.SKILL_NAME)
    os.makedirs(foreign)
    open(os.path.join(foreign, "notes.md"), "w").write("keep")
    try:
        setup.install_skill(os.path.dirname(foreign))
        raise AssertionError("foreign folder replaced")
    except RuntimeError:
        assert os.path.exists(os.path.join(foreign, "notes.md"))
    shutil.rmtree(base)
    shutil.rmtree(os.path.dirname(foreign))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
