FROM python:3-alpine

ENV INSTALL_PATH /opt/evtxtoelk
RUN mkdir -p $INSTALL_PATH
WORKDIR $INSTALL_PATH

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -f requirements.txt

COPY evtxtoelk.py evtxtoelk.py