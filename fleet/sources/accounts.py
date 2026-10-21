"""Account room from Upkeep's published status.json.

Partial shape: {"accounts": [<raw Upkeep account>]}; derive.accounts shapes them for the snapshot.
Stub until the accounts PR lands.
"""


class Accounts:
    name = "accounts"
    interval = 30

    def __init__(self, url):
        self.url = url

    def collect(self, hub):
        return {"accounts": []}
