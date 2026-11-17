"""A local launcher and an explicit identity-simulation CLI for the learning fixture."""

import argparse
import secrets
import asyncio

from .knowledge import KnowledgeService
from .repository import FixtureRepository


def main():
    parser = argparse.ArgumentParser(description="B2B SLA Escalation/Deal desk Agent")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Launch local API with temporary employee tokens")
    demo.add_argument("--port", type=int, default=8001)
    commands.add_parser("db-init", help="Apply database migrations and seed missing fixture records")
    demo.add_argument("--ephemeral", action="store_true", help="Use the original in-memory fixture without history")
    commands.add_parser("validate", help="Validate fixture data and relationships")
    search = commands.add_parser("search", help="Simulate an employee locally (not an auth endpoint)")
    search.add_argument("--employee", required=True)
    search.add_argument("--language", choices=("en", "zh"), default="en")
    search.add_argument("query")
    ask = commands.add_parser("ask", help="Ask Gemini to select knowledge tools for a simulated employee")
    ask.add_argument("--employee", required=True)
    ask.add_argument("--language", choices=("en", "zh"), default="en")
    ask.add_argument("--mode", choices=("supervisor", "single"), default="supervisor")
    ask.add_argument("question")
    demo.add_argument('--api-only',action='store_true',help='Accept durable work; run separate worker processes')
    worker=commands.add_parser('worker',help='Run durable workflow workers; SIGTERM drains current work')
    worker.add_argument('--slots',type=int,choices=range(1,9),default=2)
    backup=commands.add_parser('backup',help='Back up quiesced PostgreSQL and private artifacts')
    backup.add_argument('destination');backup.add_argument('--files',help='Artifact root (defaults to WORK_FILES_DIR or .work_assistant/files)')
    backup.add_argument('--quiesced',action='store_true');backup.add_argument('--docker',action='store_true')
    restore=commands.add_parser('restore',help='Restore into a new northstar_restore_* database and artifact directory')
    restore.add_argument('backup');restore.add_argument('--name',required=True);restore.add_argument('--files',required=True)
    restore.add_argument('--docker',action='store_true')
    args = parser.parse_args()
    repository = FixtureRepository.load()
    if args.command in ('worker','backup','restore'):
        from .db.store import Database,database_url
        url=database_url()
        if args.command=='worker':
            from .runtime.worker import run
            db=Database(url)
            try: run(db,args.slots)
            finally: db.close()
        else:
            import json
            from .runtime.backup import backup,restore
            if args.command=='backup':
                import os
                result=backup(url,args.files or os.getenv('WORK_FILES_DIR','.work_assistant/files'),args.destination,quiesced=args.quiesced,docker=args.docker)
                print(json.dumps({'backup':args.destination,'files_verified':len(result['files'])}))
            else:
                _,result=restore(url,args.backup,args.name,args.files,docker=args.docker)
                print(json.dumps(result))
    elif args.command == "db-init":
        from .db.store import Database, database_url, migrate
        url = database_url()
        migrate(url)
        db = Database(url)
        try:
            db.seed(repository.workspace)
        finally:
            db.close()
        print("Database migrated and product seed records initialized.")
    elif args.command == "validate":
        data = repository.workspace
        print(f"{data.fixture_version}: {len(data.organizations)} organizations, "
              f"{len(data.employees)} employees, {len(data.documents)} documents; valid.")
    elif args.command == "search":
        try:
            result = KnowledgeService(repository).search(args.employee, args.query, args.language)
        except PermissionError as exc:
            parser.error(str(exc))
        print(result.model_dump_json(indent=2))
    elif args.command == "ask":
        from .agent import AgentRequest, KnowledgeAgent, configured_model
        import anyio

        knowledge = KnowledgeService(repository)
        try:
            knowledge._actor(args.employee)
        except PermissionError as exc:
            parser.error(str(exc))
        model = configured_model()
        if model is None:
            parser.error("Set GOOGLE_API_KEY in .env to enable Gemini tool calling")

        async def run():
            try:
                with anyio.fail_after(90):
                    from .agents.runtime import CollaborationAgent
                    result = await CollaborationAgent(knowledge, model).ask(args.employee,
                        AgentRequest(question=args.question, language=args.language, mode=args.mode))
                print(result.model_dump_json(indent=2))
            finally:
                await model.aclose()

        try:
            asyncio.run(run())
        except Exception as exc:
            # Do not echo provider exception bodies that may include request details.
            parser.exit(1, f"Agent request failed ({type(exc).__name__}); check model access or retry.\n")
    else:
        import uvicorn
        from .api import create_app

        employee_order = {e.id: i for i, e in enumerate(repository.employees())}
        url = None
        if not args.ephemeral:
            from .db.store import Database, database_url
            url = database_url()
            repository = Database(url)
        tokens = {secrets.token_urlsafe(24): employee.id for employee in sorted(repository.employees(), key=lambda e: employee_order.get(e.id, 999))
                  if employee.active}
        print("Local simulation only. Tokens expire when this process stops.", flush=True)
        for token, employee_id in tokens.items():
            print(f"{employee_id}: {token}", flush=True)
        print(f"Open http://127.0.0.1:{args.port}/ for the workspace; /docs for the API.", flush=True)
        if url:
            repository.close()
        uvicorn.run(create_app(tokens, repository if not url else None, demo_mode=True, database_url=url, embedded_worker=not args.api_only), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
