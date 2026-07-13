"""CSV snippet preview tests."""

from app.ingestion import csv_text_snippet


def test_csv_text_snippet_returns_headers_and_rows():
    content = b"id,asset,amount\n1,BTC,0.5\n2,ETH,1.0\n3,SOL,10\n"
    snippet = csv_text_snippet(content, max_rows=2, max_cols=3)
    assert snippet is not None
    assert snippet["columns"] == ["id", "asset", "amount"]
    assert snippet["rows"] == [["1", "BTC", "0.5"], ["2", "ETH", "1.0"]]
    assert snippet["total_rows"] == 3
    assert snippet["total_columns"] == 3
    assert snippet["truncated_columns"] is False
