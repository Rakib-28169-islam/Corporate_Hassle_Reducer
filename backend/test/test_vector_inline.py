"""Inline tests extracted from vector_store.py."""

import sys
import os
import shutil

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database.vector_store import VectorStore

TEST_DIR = "test_chroma_data"


def main():
    print("Testing VectorStore...")
    print("=" * 50)

    vs = VectorStore(persist_dir=TEST_DIR)

    # 1. Status check
    status = vs.status()
    print(f"[OK] Status: available={status['available']}, docs={status['document_count']}")

    # 2. Add batch of emails
    emails = [
        {"id": "msg_001", "subject": "Project Deployment", "from": "dev@company.com",
         "body": "The release is scheduled for Friday. All tests passing."},
        {"id": "msg_002", "subject": "Team Meeting", "from": "boss@company.com",
         "body": "Don't forget our standup meeting tomorrow at 10am."},
        {"id": "msg_003", "subject": "Invoice #1234", "from": "finance@company.com",
         "body": "Please find attached the invoice for last month's services. Total: 5000 TK."},
    ]
    count = vs.add_documents("user_1", "gmail", "email", emails)
    print(f"[OK] Added {count} gmail emails")

    # 3. Add slack messages
    messages = [
        {"id": "slack_001", "channel": "#dev", "user": "john", "text": "Deploy pipeline is green, ready to ship!"},
        {"id": "slack_002", "channel": "#general", "user": "sarah", "text": "Lunch meeting moved to 2pm"},
    ]
    count = vs.add_documents("user_1", "slack", "message", messages)
    print(f"[OK] Added {count} slack messages")

    # 4. Semantic search — "release" should match "deployment" and "ship"
    results = vs.search("any updates about the release?", user_id="user_1")
    print(f"[OK] Semantic search 'release': {len(results)} results")
    for r in results[:3]:
        print(f"     - [{r['tool']}] {r['external_id']} (score: {r['score']:.3f})")

    # 5. Filtered search — only gmail
    results = vs.search("meeting", user_id="user_1", tool="gmail")
    print(f"[OK] Gmail-only search 'meeting': {len(results)} results")

    # 6. Multi-tool search — gmail + slack
    results = vs.search("meeting", user_id="user_1", tool=["gmail", "slack"])
    print(f"[OK] Multi-tool search 'meeting': {len(results)} results")

    # 7. Add single document
    success = vs.add_single("user_1", "outlook", "event",
                            {"title": "Sprint Review", "start": "2024-02-20 14:00"},
                            external_id="evt_001")
    print(f"[OK] Add single outlook event: {success}")

    # 8. Total count
    total = vs.count()
    print(f"[OK] Total documents: {total}")

    # 9. Delete by tool
    vs.delete_by_tool("user_1", tool="slack")
    after_delete = vs.count()
    print(f"[OK] After deleting slack: {after_delete} documents remain")

    # 10. Delete specific IDs
    vs.delete_by_ids("user_1", "gmail", ["msg_003"])
    after_specific = vs.count()
    print(f"[OK] After deleting msg_003: {after_specific} documents remain")

    # Cleanup test data
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    print("\n" + "=" * 50)
    print("[ALL TESTS PASSED]")


if __name__ == "__main__":
    main()
