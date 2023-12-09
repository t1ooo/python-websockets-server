.PHONY: install
install:
	poetry install


.PHONY: test
test:
	poetry run pytest


.PHONY: example_web_chat
example_web_chat:
	poetry run python -m examples.web_chat.web_chat