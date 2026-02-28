# Disable web3's pytest_ethereum plugin — it registers automatically via
# setuptools entrypoints but requires an older eth-typing that conflicts
# with py-clob-client's dependency resolution.
collect_ignore_glob = []


def pytest_configure(config):
    config.pluginmanager.set_blocked("pytest_ethereum")
