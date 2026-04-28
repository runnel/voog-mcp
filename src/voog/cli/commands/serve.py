"""serve command — stub. Implemented in Task 11/12/13."""
def add_arguments(subparsers):
    p = subparsers.add_parser("serve", help="Serve templates locally with auto-upload")
    p.set_defaults(func=run)


def run(args, client=None):
    raise NotImplementedError("serve not yet implemented")
