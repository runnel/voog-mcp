"""redirects command — stub. Implemented in Task 11/12/13."""
def add_arguments(subparsers):
    p = subparsers.add_parser("redirects", help="Manage Voog redirects")
    p.set_defaults(func=run)


def run(args, client=None):
    raise NotImplementedError("redirects not yet implemented")
