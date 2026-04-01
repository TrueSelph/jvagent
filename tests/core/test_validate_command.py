"""CLI ``jvagent validate`` behavior."""


def test_run_validate_ok_minimal_app(tmp_path):
    from jvagent.cli.commands import run_validate

    (tmp_path / "app.yaml").write_text(
        "app: minimal_test\nagents: []\n", encoding="utf-8"
    )
    assert run_validate(str(tmp_path)) == 0


def test_run_validate_fails_on_unknown_config_section(tmp_path):
    from jvagent.cli.commands import run_validate

    (tmp_path / "app.yaml").write_text(
        "\n".join(
            [
                "app: x",
                "agents: []",
                "config:",
                "  not_a_real_section: {}",
            ]
        ),
        encoding="utf-8",
    )
    assert run_validate(str(tmp_path)) == 1
