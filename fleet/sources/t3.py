"""T3 threads from every configured server, through each server's own MCP tools.

Partial shape: {"servers": [<snapshot server>], "threads": [<normalized thread>]}.
A normalized thread is:
  {"server", "threadId", "projectId", "project", "title", "status", "parentThreadId",
   "settled", "snoozed", "account", "model", "itemCount", "latestRunId",
   "createdAt", "updatedAt", "prs": [{"repository", "number", "url", "state"}],
   "pendingQuestions": int}
Stub until the T3 source PR lands.
"""


class T3:
    name = "t3"

    def __init__(self, config, clients):
        self.interval = config.interval_seconds
        self.clients = clients

    def collect(self, hub):
        return {"servers": [], "threads": []}
