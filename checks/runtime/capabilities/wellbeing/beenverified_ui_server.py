import asyncio
import json
import socketserver
import threading
from contextlib import closing

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_broker_beenverified import (
    register,
)
from gideon.workspace.capabilities.communications import PeopleStore, mirrors
from gideon.workspace.capabilities.communications.outbound_email import (
    OutboundEmail,
    SMTPTransport,
)
from gideon.workspace.capabilities.wellbeing.privacy import PrivacyStore
from gideon.workspace.capabilities.wellbeing.privacy_broker_beenverified import (
    RECIPIENT,
    BeenVerifiedCaseAdapter,
)
from gideon.workspace.capabilities.wellbeing.privacy_brokers import (
    BrokerAdapterResult,
    PrivacyBrokerStore,
)

captured = []


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 local\r\n")
        sender = ""
        recipients = []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            value = line.decode(errors="replace").rstrip("\r\n")
            upper = value.upper()
            if upper.startswith("EHLO "):
                self.wfile.write(b"250-local\r\n250 SIZE 20971520\r\n")
            elif upper.startswith("MAIL FROM:"):
                sender = value.split(":", 1)[1].split()[0].strip("<>")
                self.wfile.write(b"250 ok\r\n")
            elif upper.startswith("RCPT TO:"):
                recipients.append(value.split(":", 1)[1].strip("<>"))
                self.wfile.write(b"250 ok\r\n")
            elif upper == "DATA":
                self.wfile.write(b"354 data\r\n")
                raw = bytearray()
                while True:
                    part = self.rfile.readline()
                    if part == b".\r\n":
                        break
                    raw.extend(part[1:] if part.startswith(b"..") else part)
                captured.append(
                    {
                        "sender": sender,
                        "recipients": recipients,
                        "data": bytes(raw).decode(errors="replace"),
                    }
                )
                self.wfile.write(b"250 accepted\r\n")
            elif upper == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


async def main():
    smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SMTPHandler)
    thread = threading.Thread(target=smtp.serve_forever, daemon=True)
    thread.start()
    root = config_dir()
    privacy = PrivacyStore(root)
    subject = privacy.create_subject(
        {
            "request_id": "subject",
            "alias": "Owner",
            "relationship": "self",
            "source": "owner",
        }
    )
    for scope in ("broker_scan", "broker_submit"):
        privacy.consent(
            subject["id"],
            {
                "request_id": scope,
                "revision": 0,
                "scope": scope,
                "granted": True,
                "method": "owner choice",
            },
        )
    store = PrivacyBrokerStore(root)
    broker = store.create_broker(
        {
            "request_id": "broker",
            "name": "BeenVerified",
            "website": "https://www.beenverified.com",
            "optout_url": "https://www.beenverified.com/svc/optout/search/optouts",
            "source": "curated",
        }
    )
    case = store.create_case(
        subject["id"], {"request_id": "case", "broker_id": broker["id"]}
    )
    case = store.apply_adapter_scan(
        case["id"],
        {"request_id": "scan", "revision": case["revision"]},
        BrokerAdapterResult(
            "fixture-scan", "found", "2026-09-25T12:00:00+00:00", "sha256:" + "a" * 64
        ),
    )
    communications = PeopleStore()
    account = mirrors.save_account(
        communications,
        {
            "name": "Owner email",
            "kind": "imap",
            "owner_email": "owner@example.com",
            "alias": "custom",
            "host": "imap.example.com",
            "username": "owner@example.com",
            "credential_ref": "OWNER_EMAIL",
            "auth_mode": "password",
            "inbox_folder": "INBOX",
            "sent_folder": "Sent",
        },
    )
    outbound = OutboundEmail(
        communications,
        transport=SMTPTransport(
            host="127.0.0.1",
            port=smtp.server_address[1],
            starttls=False,
            authenticate=False,
        ),
    )
    service = BeenVerifiedCaseAdapter(store, communications, outbound)
    app = web.Application()
    register(app, adapter=service)

    async def accounts(_):
        return web.json_response({"accounts": mirrors.accounts(communications)})

    async def config(_):
        return web.json_response(
            {"case": store.get_case(case["id"]), "account": account}
        )

    async def messages(_):
        return web.json_response({"messages": captured})

    async def reply(request):
        link = service._link(case["id"])
        draft = outbound._get(link["draft_id"])
        body = (await request.json()).get(
            "body", "Verification code 482913. Visit https://untrusted.example/verify"
        )
        raw = (
            f'From: {RECIPIENT}\r\nTo: owner@example.com\r\nMessage-ID: <ui-beenverified-reply@example.com>\r\nIn-Reply-To: {draft["message_id"]}\r\nSubject: Verify request\r\n\r\n{body}\r\n'
        ).encode()
        row = mirrors.normalize_message(raw, account["owner_email"], "fallback")
        with closing(communications.connect()) as db, db:
            mirrors.schema(db)
            db.execute(
                "INSERT OR REPLACE INTO mirror_messages VALUES (?,?,?)",
                (account["id"], row["external_id"], json.dumps(row)),
            )
        return web.json_response({"message": row})

    app.router.add_get("/api/capabilities/communications/mirror/accounts", accounts)
    app.router.add_get("/contract/config", config)
    app.router.add_get("/contract/messages", messages)
    app.router.add_post("/contract/reply", reply)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    print(json.dumps({"port": port}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        smtp.shutdown()
        smtp.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    asyncio.run(main())
