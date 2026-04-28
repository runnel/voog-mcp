"""layouts command — stub. Implemented in Task 11/12/13."""
def add_arguments(subparsers):
    p = subparsers.add_parser("layouts", help="Manage Voog layouts")
    p.set_defaults(func=run)


def run(args, client=None):
    raise NotImplementedError("layouts not yet implemented")
