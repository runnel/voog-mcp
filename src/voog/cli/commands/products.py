"""products command — stub. Implemented in Task 11/12/13."""
def add_arguments(subparsers):
    p = subparsers.add_parser("products", help="Manage Voog products")
    p.set_defaults(func=run)


def run(args, client=None):
    raise NotImplementedError("products not yet implemented")
