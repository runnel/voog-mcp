"""pull command — stub. Implemented in Task 11/12/13."""
def add_arguments(subparsers):
    p = subparsers.add_parser("pull", help="Download templates from Voog")
    p.set_defaults(func=run)


def run(args, client=None):
    raise NotImplementedError("pull not yet implemented")
