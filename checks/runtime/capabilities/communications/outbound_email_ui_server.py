import asyncio
import json
import socketserver
import threading
from contextlib import closing

from aiohttp import web

from gideon.interfaces.dashboard.handlers.capabilities_communications_outbound import (
    register,
)
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.communications import PeopleStore, mirrors
from gideon.workspace.capabilities.communications.outbound_email import (
    OutboundEmail,
    SMTPTransport,
)

captured = []


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 local SMTP\r\n")
        sender, recipients = "", []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            command = line.decode(errors="replace").rstrip("\r\n")
            if command.upper().startswith("EHLO "):
                self.wfile.write(b"250-local\r\n250 SIZE 20971520\r\n")
            elif command.upper().startswith("MAIL FROM:"):
                sender = command.split(":", 1)[1].split()[0].strip("<>")
                self.wfile.write(b"250 ok\r\n")
            elif command.upper().startswith("RCPT TO:"):
                recipients.append(command.split(":", 1)[1].strip("<>"))
                self.wfile.write(b"250 ok\r\n")
            elif command.upper() == "DATA":
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
                        "data": bytes(raw).decode("utf-8", errors="replace"),
                    }
                )
                self.wfile.write(b"250 accepted\r\n")
            elif command.upper() == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


async def main():
    smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SMTPHandler)
    thread = threading.Thread(target=smtp.serve_forever, daemon=True)
    thread.start()
    store = PeopleStore()
    account = mirrors.save_account(
        store,
        {
            "name": "Customer mail",
            "kind": "imap",
            "owner_email": "owner@example.com",
            "alias": "custom",
            "host": "imap.example.com",
            "username": "owner@example.com",
            "credential_ref": "CUSTOMER_MAIL",
            "auth_mode": "password",
            "inbox_folder": "INBOX",
            "sent_folder": "Sent",
        },
    )
    service = OutboundEmail(
        store,
        artifacts=NativeArtifactProvider(),
        transport=SMTPTransport(
            host="127.0.0.1",
            port=smtp.server_address[1],
            starttls=False,
            authenticate=False,
        ),
    )
    app = web.Application()
    register(app, service)

    async def accounts(_request):
        return web.json_response({"accounts": mirrors.accounts(store)})

    async def contract(_request):
        return web.json_response({"messages": captured})

    async def reply(request):
        draft = service._get((await request.json())["draft_id"])
        raw = (
            f'From: privacyrequest@whitepages.com\r\nTo: {draft["sender"]}\r\nMessage-ID: <ui-reply@whitepages.com>\r\n'
            f'In-Reply-To: {draft["message_id"]}\r\nSubject: Re: {draft["subject"]}\r\n\r\n'
            "Use https://untrusted.example/verify only after review.\r\n"
        ).encode()
        row = mirrors.normalize_message(raw, account["owner_email"], "ui-fallback")
        with closing(store.connect()) as db, db:
            mirrors.schema(db)
            db.execute(
                "INSERT OR REPLACE INTO mirror_messages VALUES (?,?,?)",
                (account["id"], row["external_id"], json.dumps(row)),
            )
        return web.json_response({"message": row})

    app.router.add_get("/api/capabilities/communications/mirror/accounts", accounts)
    app.router.add_get("/contract/messages", contract)
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
