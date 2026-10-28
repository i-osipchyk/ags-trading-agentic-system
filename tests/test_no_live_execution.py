from pathlib import Path

FORBIDDEN = (
    "ProtoOANewOrderReq",
    "ProtoOAAmendPositionSLTPReq",
    "ProtoOAClosePositionReq",
    "ProtoOACancelOrderReq",
    "ProtoOAAmendOrderReq",
)

SRC = Path(__file__).resolve().parent.parent / "src"
# The one module allowed to place orders: it only connects to a cTrader demo account.
GATEWAY = SRC / "ags" / "portfolio" / "demo_gateway.py"


def find_trading_messages(root: Path, allowed: tuple[Path, ...] = ()) -> list[tuple[Path, str]]:
    return [
        (path, name)
        for path in sorted(root.rglob("*.py"))
        if path not in allowed
        for name in FORBIDDEN
        if name in path.read_text()
    ]


def test_only_the_demo_gateway_references_ctrader_trading_messages():
    assert GATEWAY.is_file()
    assert find_trading_messages(SRC, allowed=(GATEWAY,)) == []


def test_the_gateway_connects_to_the_demo_host_only_and_checks_the_account_is_not_live():
    text = GATEWAY.read_text()
    assert "PROTOBUF_DEMO_HOST" in text and "PROTOBUF_LIVE_HOST" not in text
    assert "isLive" in text


def test_guard_flags_a_file_referencing_a_trading_message(tmp_path):
    (tmp_path / "sneaky.py").write_text("from x import ProtoOANewOrderReq\n")

    assert find_trading_messages(tmp_path) == [(tmp_path / "sneaky.py", "ProtoOANewOrderReq")]
