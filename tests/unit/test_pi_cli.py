from myaibot.agents.pi_cli import PiCliConfig, PiCliRunner


def test_pi_cli_builds_print_command():
    cfg = PiCliConfig(model="gpt-5.5", thinking="high", session_dir="sessions", allow_tools=["read", "write"])
    runner = PiCliRunner(cfg)
    args = runner._build_args("hello", continue_session=True)
    assert args[0].lower().endswith(("pi", "pi.cmd"))
    assert args[1] == "-p"
    assert "-c" in args
    assert ["--model", "gpt-5.5"] == args[args.index("--model") : args.index("--model") + 2]
    assert args[-1] == "hello"
