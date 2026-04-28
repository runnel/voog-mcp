"""snapshot command — stub. Implemented in Task 11/12/13."""
def add_arguments(subparsers):
    p = subparsers.add_parser("snapshot", help="Export a full site snapshot")
    p.set_defaults(func=run)


def run(args, client=None):
    raise NotImplementedError("snapshot not yet implemented")
