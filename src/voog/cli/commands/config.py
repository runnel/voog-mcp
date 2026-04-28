"""config command — stub. Implemented in Task 11."""
def add_arguments(subparsers):
    p = subparsers.add_parser("config", help="Manage global configuration")
    sub = p.add_subparsers(dest="config_action", required=True)
    init = sub.add_parser("init", help="Interactively create voog.json + .env")
    init.set_defaults(func=lambda args: 0)
    list_p = sub.add_parser("list-sites", help="List configured sites")
    list_p.set_defaults(func=lambda args: 0)
    check = sub.add_parser("check", help="Verify all configured tokens (HEAD per site)")
    check.set_defaults(func=lambda args: 0)
    p.set_defaults(func=lambda args: 0)
