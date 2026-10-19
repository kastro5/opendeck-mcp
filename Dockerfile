# For MCP directories (Glama) that check the server starts and lists its tools.
#
# It isn't a way to run opendeck-mcp for real: the server has to run on the PC
# with OpenDeck, inside the desktop session, to read its config and restart it.
# In a container there is no OpenDeck, so the tools load but report that
# OpenDeck's folders weren't found.
FROM python:3.13-slim

RUN pip install --no-cache-dir opendeck-mcp==0.4.0

ENTRYPOINT ["opendeck-mcp"]
