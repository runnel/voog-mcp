"""list command — stub. Implemented in Task 11/12/13."""
def add_arguments(subparsers):
    p = subparsers.add_parser("list", help="List Voog templates")
    p.set_defaults(func=run)


def run(args, client=None):
    raise NotImplementedError("list not yet implemented")
