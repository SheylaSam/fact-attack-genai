.PHONY: run build test benchmark compare download install web setup-web api streamlit

# Forward all targets to track_2a/Makefile
run:
	$(MAKE) -C track_2a run

test:
	$(MAKE) -C track_2a test

install:
	$(MAKE) -C track_2a install

download:
	$(MAKE) -C track_2a download

benchmark:
	$(MAKE) -C track_2a benchmark

compare:
	$(MAKE) -C track_2a compare

web:
	$(MAKE) -C track_2a web

setup-web:
	$(MAKE) -C track_2a setup-web

api:
	$(MAKE) -C track_2a api

streamlit:
	$(MAKE) -C track_2a streamlit
