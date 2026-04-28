"""pages command — stub. Implemented in Task 11/12/13."""
def add_arguments(subparsers):
    p = subparsers.add_parser("pages", help="Manage Voog pages")
    p.set_defaults(func=run)


def run(args, client=None):
    raise NotImplementedError("pages not yet implemented")
