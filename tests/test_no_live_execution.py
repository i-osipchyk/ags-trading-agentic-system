from pathlib import Path

FORBIDDEN = (
    "ProtoOANewOrderReq",
    "ProtoOAAmendPositionSLTPReq",
    "ProtoOAClosePositionReq",
    "ProtoOACancelOrderReq",
    "ProtoOAAmendOrderReq",
)

SRC = Path(__file__).resolve().parent.parent / "src"


def find_trading_messages(root: Path) -> list[tuple[Path, str]]:
    return [
        (path, name)
        for path in sorted(root.rglob("*.py"))
        for name in FORBIDDEN
        if name in path.read_text()
    ]


def test_src_never_references_ctrader_trading_messages():
    assert find_trading_messages(SRC) == []


def test_guard_flags_a_file_referencing_a_trading_message(tmp_path):
    (tmp_path / "sneaky.py").write_text("from x import ProtoOANewOrderReq\n")

    assert find_trading_messages(tmp_path) == [(tmp_path / "sneaky.py", "ProtoOANewOrderReq")]
