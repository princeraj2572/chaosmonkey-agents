.PHONY: test demo clean

PYTHON ?= python3
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src

test:
	$(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) -m chaosmonkey run inventory --seeds 1,2,3 --fault-rate 0.35 --format terminal >/dev/null

demo:
	@mkdir -p out
	$(PYTHON) -m chaosmonkey run inventory --model mock-naive --runs 9 --seed 7 --out out/naive >/dev/null
	$(PYTHON) -m chaosmonkey run inventory --model mock-careful --runs 9 --seed 7 --out out/careful >/dev/null
	$(PYTHON) -m chaosmonkey compare out/naive/results.json out/careful/results.json --out out/compare.html

clean:
	rm -rf out build dist
