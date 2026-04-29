"""Tests for .env auto-loading in load_blueprint_yaml."""

from pathlib import Path


from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _write_blueprint(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "agent.yml"
    p.write_text(content, encoding="utf-8")
    return p


class TestDotenvAutoLoad:
    def test_loads_dotenv_from_blueprint_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MY_RG", raising=False)
        (tmp_path / ".env").write_text("MY_RG=prod-rg\n")
        bp = _write_blueprint(tmp_path, _blueprint_with("${env.MY_RG}"))
        raw = load_blueprint_yaml(bp)
        assert raw["deploy"]["azure"]["resource_group"] == "prod-rg"

    def test_existing_env_var_takes_precedence_over_dotenv(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_RG", "from-shell")
        (tmp_path / ".env").write_text("MY_RG=from-dotenv\n")
        bp = _write_blueprint(tmp_path, _blueprint_with("${env.MY_RG}"))
        raw = load_blueprint_yaml(bp)
        assert raw["deploy"]["azure"]["resource_group"] == "from-shell"

    def test_falls_back_to_cwd_dotenv(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MY_RG", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("MY_RG=cwd-rg\n")
        # Blueprint in a subdir — no .env next to it
        subdir = tmp_path / "sub"
        subdir.mkdir()
        bp = _write_blueprint(subdir, _blueprint_with("${env.MY_RG}"))
        raw = load_blueprint_yaml(bp)
        assert raw["deploy"]["azure"]["resource_group"] == "cwd-rg"

    def test_no_dotenv_leaves_placeholder(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MY_RG", raising=False)
        bp = _write_blueprint(tmp_path, _blueprint_with("${env.MY_RG}"))
        raw = load_blueprint_yaml(bp)
        # No .env, not in env → placeholder kept as-is
        assert raw["deploy"]["azure"]["resource_group"] == "${env.MY_RG}"

    def test_dotenv_ignores_comments_and_blank_lines(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MY_RG", raising=False)
        (tmp_path / ".env").write_text(
            "# this is a comment\n\nMY_RG=clean-rg\n"
        )
        bp = _write_blueprint(tmp_path, _blueprint_with("${env.MY_RG}"))
        raw = load_blueprint_yaml(bp)
        assert raw["deploy"]["azure"]["resource_group"] == "clean-rg"

    def test_blueprint_dir_dotenv_wins_over_cwd(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MY_RG", raising=False)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        (cwd / ".env").write_text("MY_RG=from-cwd\n")
        subdir = tmp_path / "project"
        subdir.mkdir()
        (subdir / ".env").write_text("MY_RG=from-blueprint-dir\n")
        bp = _write_blueprint(subdir, _blueprint_with("${env.MY_RG}"))
        raw = load_blueprint_yaml(bp)
        assert raw["deploy"]["azure"]["resource_group"] == "from-blueprint-dir"


def _blueprint_with(resource_group_value: str) -> str:
    return f"""\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n: {{}}
  edges: []
deploy:
  azure:
    resource_group: "{resource_group_value}"
    acr_name: "myregistry"
    container_app_env: "my-env"
"""


class TestHarnessFileLoading:
    def test_loads_harness_from_external_file(self, tmp_path):
        (tmp_path / "harness.yml").write_text(
            """\
defaults:
  llm_mode: mock
scenarios:
  - id: refund_happy_path
    input:
      message: "refund"
    expected:
      route: billing
""",
            encoding="utf-8",
        )
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
harness:
  file: "harness.yml"
""",
        )

        raw = load_blueprint_yaml(blueprint)
        assert raw["harness"]["defaults"]["llm_mode"] == "mock"
        assert raw["harness"]["scenarios"][0]["id"] == "refund_happy_path"
        assert "file" not in raw["harness"]

    def test_merges_inline_harness_over_external_defaults(self, tmp_path):
        (tmp_path / "harness.yml").write_text(
            """\
defaults:
  llm_mode: replay
  tool_mode: replay
scenarios:
  - id: external_case
    input: {message: "external"}
    expected: {route: billing}
""",
            encoding="utf-8",
        )
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
harness:
  file: "harness.yml"
  defaults:
    tool_mode: stub
  scenarios:
    - id: inline_case
      input:
        message: "inline"
      expected:
        route: support
""",
        )

        raw = load_blueprint_yaml(blueprint)
        assert raw["harness"]["defaults"]["llm_mode"] == "replay"
        assert raw["harness"]["defaults"]["tool_mode"] == "stub"
        assert [scenario["id"] for scenario in raw["harness"]["scenarios"]] == [
            "external_case",
            "inline_case",
        ]

    def test_supports_multiple_harness_files(self, tmp_path):
        (tmp_path / "a.yml").write_text(
            """\
scenarios:
  - id: a
    input: {}
    expected: {}
""",
            encoding="utf-8",
        )
        (tmp_path / "b.yml").write_text(
            """\
scenarios:
  - id: b
    input: {}
    expected: {}
""",
            encoding="utf-8",
        )
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
harness:
  files:
    - "a.yml"
    - "b.yml"
""",
        )

        raw = load_blueprint_yaml(blueprint)
        assert [scenario["id"] for scenario in raw["harness"]["scenarios"]] == ["a", "b"]

    def test_resolves_replay_trace_paths_relative_to_harness_sources(self, tmp_path):
        traces = tmp_path / "traces"
        traces.mkdir()
        (traces / "golden.json").write_text("{}", encoding="utf-8")
        (tmp_path / "harness.yml").write_text(
            """\
defaults:
  replay_trace: "traces/golden.json"
scenarios:
  - id: replay_case
    input: {}
    expected: {}
    replay_trace: "traces/golden.json"
""",
            encoding="utf-8",
        )
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
harness:
  file: "harness.yml"
""",
        )

        raw = load_blueprint_yaml(blueprint)
        expected = str((traces / "golden.json").resolve())
        assert raw["harness"]["defaults"]["replay_trace"] == expected
        assert raw["harness"]["scenarios"][0]["replay_trace"] == expected
