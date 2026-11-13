from language.router import Languages
from db.model.symbol import Symbol
from argparse import ArgumentParser
from config import Config
from db import Database
import sys

import logging

log = logging.getLogger("ciximport")
logging.basicConfig(stream=sys.stdout, level=Config.get("logging_level", logging.INFO))

class Main:

    def __init__(self):
        parser = ArgumentParser(description='Cix Importer')

        parser.add_argument('-l', "--language", type=str,
                            help='Language to use')

        parser.add_argument('-c', "--cix", type=str,
                            help='Path to cix file')

        parser.add_argument('-f', "--file", type=str,
                            help='Specify the database file to use')

        args = parser.parse_args()

        if not args.language:
            log.exception("language (--language) is required")
            sys.exit(1)

        if not args.file:
            log.exception("database file (--file) is required")
            sys.exit(1)

        Database.initialize(args.file, Config.get("closure_ext_path"))

        languages = []
        if args.language == "all":
            for language in Languages.getLanguages():
                languages.append(language.name)
        else:
            languages = [args.language]

        for name in languages:
            language = Languages.getLanguage(name)

            cix = args.cix
            if not cix:
                cix = language.cix

            if not cix:
                continue

            if not isinstance(cix, (list, tuple)):
                log.info("Importing %s into %s" % (name, cix))

                language.stdlib.scan(cix)
            else:
                cixes = cix
                for cix in cixes:
                    log.info("Importing %s into %s" % (name, cix))

                    language.stdlib.scan(cix)

            catalogs = language.catalogs
            if catalogs:
                for catalog in catalogs:
                    log.info("Importing catalog %s for %s" % (catalog, name))
                    language.stdlib.scan(catalog)

        if (Database.conn.get_conn()):
            Database.conn.close()

if __name__ == "__main__":
    Main()
