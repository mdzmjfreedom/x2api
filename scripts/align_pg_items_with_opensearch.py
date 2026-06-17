from __future__ import annotations


def main() -> int:
    print(
        "scripts/align_pg_items_with_opensearch.py has been retired after the OpenSearch hard cutover. "
        "Use direct dual-write collectors plus explicit SQL column drops instead."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
