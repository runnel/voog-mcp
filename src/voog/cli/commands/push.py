"""push command — stub. Implemented in Task 11/12/13."""
def add_arguments(subparsers):
    p = subparsers.add_parser("push", help="Upload templates to Voog")
    p.set_defaults(func=run)


def run(args, client=None):
    raise NotImplementedError("push not yet implemented")
