import contextlib
import mmap
import traceback
import json
import os
import argparse
from collections import OrderedDict
from datetime import datetime

from Evtx.Evtx import FileHeader
from Evtx.Views import evtx_file_xml_view
from elasticsearch import Elasticsearch, helpers
import xmltodict
import sys


class EvtxToElk:
    @staticmethod
    def bulk_to_elasticsearch(es, bulk_queue):
        try:
            helpers.bulk(es, bulk_queue)
            return True
        except:
            print(traceback.print_exc())
            return False

    @staticmethod
    def evtx_to_elk(filename, elk_ip, elk_index="hostlogs", bulk_queue_len_threshold=500, metadata={}):
        bulk_queue = []
        es = Elasticsearch([elk_ip])
        with open(filename) as infile:
            with contextlib.closing(mmap.mmap(infile.fileno(), 0, access=mmap.ACCESS_READ)) as buf:
                fh = FileHeader(buf, 0x0)
                data = ""
                for xml, record in evtx_file_xml_view(fh):
                    try:
                        contains_event_data = False
                        log_line = xmltodict.parse(xml)

                        # Format the date field
                        date = log_line.get("Event").get("System").get("TimeCreated").get("@SystemTime")
                        if "." not in str(date):
                            date = datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
                        else:
                            date = datetime.strptime(date, "%Y-%m-%d %H:%M:%S.%f")
                        log_line['@timestamp'] = str(datetime.now().isoformat())
                        log_line["Event"]["System"]["TimeCreated"]["@SystemTime"] = str(date.isoformat())

                        # del log_line["System"] 

                        # Process the data field to be searchable
                        data = ""
                        if log_line.get("Event") is not None:
                            log_line["winlog"] = log_line.pop("Event")
                            log_line["winlog"]["time_created"] = log_line["winlog"]["System"]["TimeCreated"].pop("@SystemTime")
                            del log_line["winlog"]["System"]["TimeCreated"]
                            data = log_line.get("winlog")
                            if log_line.get("winlog").get("EventData") is not None:
                                log_line["winlog"]["event_data"] = log_line["winlog"].pop("EventData")
                                data = log_line.get("winlog").get("event_data")
                                if log_line.get("winlog").get("event_data").get("Data") is not None:
                                    log_line["winlog"]["event_data"] = log_line["winlog"]["event_data"].pop("Data")
                                    data = log_line.get("winlog").get("event_data")
                                    
                                    if isinstance(data, list):
                                        contains_event_data = True
                                        data_vals = {}
                                        for dataitem in data:
                                            try:
                                                if dataitem.get("@Name") is not None:
                                                    data_vals[str(dataitem.get("@Name"))] = str(
                                                        str(dataitem.get("#text")))
                                            except:
                                                pass
                                        log_line["winlog"]["event_data"] = data_vals
                                    else:
                                        if isinstance(data, OrderedDict):
                                            log_line["winlog"]["event_data"]["RawData"] = json.dumps(data)
                                        else:
                                            log_line["winlog"]["event_data"]["RawData"] = str(data)
                                else:
                                    if isinstance(data, OrderedDict):
                                        log_line["winlog"]["RawData"] = json.dumps(data)
                                    else:
                                        log_line["winlog"]["RawData"] = str(data)
                            else:
                                if isinstance(data, OrderedDict):
                                    log_line = dict(data)
                                else:
                                    log_line["RawData"] = str(data)
                        else:
                            pass

                        # Insert data into queue
                        #event_record = json.loads(json.dumps(log_line))
                        #event_record.update({
                        #    "_index": elk_index,
                        #    "_type": elk_index,
                        #    "metadata": metadata
                        #})
                        #bulk_queue.append(event_record)
                        event_data = json.loads(json.dumps(log_line))
                        event_data["_index"] = elk_index
                        event_data["meta"] = metadata
                        bulk_queue.append(event_data)

                        #bulk_queue.append({
                        #    "_index": elk_index,
                        #    "_type": elk_index,
                        #    "body": json.loads(json.dumps(log_line)),
                        #    "metadata": metadata
                        #})

                        if len(bulk_queue) == bulk_queue_len_threshold:
                            print('Bulkingrecords to ES: ' + str(len(bulk_queue)))
                            # start parallel bulking to ElasticSearch, default 500 chunks;
                            if EvtxToElk.bulk_to_elasticsearch(es, bulk_queue):
                                bulk_queue = []
                            else:
                                print('Failed to bulk data to Elasticsearch')
                                sys.exit(1)

                    except:
                        print("***********")
                        print("Parsing Exception")
                        print(traceback.print_exc())
                        print(json.dumps(log_line, indent=2))
                        print("***********")

                # Check for any remaining records in the bulk queue
                if len(bulk_queue) > 0:
                    print('Bulking final set of records to ES: ' + str(len(bulk_queue)))
                    if EvtxToElk.bulk_to_elasticsearch(es, bulk_queue):
                        bulk_queue = []
                    else:
                        print('Failed to bulk data to Elasticsearch')
                        sys.exit(1)


if __name__ == "__main__":
    # Create argument parser
    parser = argparse.ArgumentParser()
    # Add arguments
    parser.add_argument('evtxfile_or_dir', help="Evtx file or directory to parse")
    parser.add_argument('elk_ip', default="localhost", help="IP (and port) of ELK instance")
    parser.add_argument('-i', default="hostlogs", help="ELK index to load data into")
    parser.add_argument('-s', default=500, help="Size of queue")
    parser.add_argument('-meta', default={}, type=json.loads, help="Metadata to add to records")
    # Parse arguments and call evtx to elk class
    args = parser.parse_args()
    if os.path.isfile(args.evtxfile_or_dir):
        print(f"Processing file {args.evtxfile_or_dir}")
        EvtxToElk.evtx_to_elk(args.evtxfile_or_dir, args.elk_ip, elk_index=args.i, bulk_queue_len_threshold=int(args.s), metadata=args.meta)
    else:
        file_list = [os.path.join(args.evtxfile_or_dir, f) for f in os.listdir(args.evtxfile_or_dir) if os.path.isfile(os.path.join(args.evtxfile_or_dir, f))]
        for evtxfile in file_list:
            if evtxfile.endswith('.evtx'):
                print(f"Processing file {evtxfile}")
                EvtxToElk.evtx_to_elk(evtxfile, args.elk_ip, elk_index=args.i, bulk_queue_len_threshold=int(args.s), metadata=args.meta)
