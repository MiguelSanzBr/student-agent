def pytest_configure(config):
    config.addinivalue_line("markers", "live: spikes ao vivo contra SDKs/serviços externos")
