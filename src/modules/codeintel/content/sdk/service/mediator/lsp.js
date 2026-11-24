(function() {

    const logging = require("ko/logging");
    const log = logging.getLogger("typescript/mediator");
    const {Cc, Ci}  = require("chrome");
    const koPromise = require("ko/promise");
    const rpc = require("contrib/vscode-jsonrpc/main");
    const child_process = require("sdk/system/child_process");
    const koShell = require("ko/shell");
    const { emit } = require('sdk/event/core');
    const appinfo = Cc["@mozilla.org/xre/app-info;1"].getService(Ci.nsIXULRuntime);
    const koEditor = require("ko/editor");
    const views = require("ko/views");
    const koFile = require("ko/file");
    const w = require("ko/windows").getMain();
    const legacy = w.ko;

    //log.setLevel(10);

    var process, connection;
    var ready = false;
    var failing = false;
    var diagnostics = {};
    var openDocs = {};
    
    module.exports = {};

    (function ()
    {
        var meta;

        this.capabilities = {
            applyEdit: false,
            workspaceEdit: {
                documentChanges: false
            },
            didChangeConfiguration: {
                dynamicRegistration: false
            },
            didChangeWatchedFiles: {
                dynamicRegistration: false
            },
            symbol: {
                dynamicRegistration: false
            },
            executeCommand: {
                dynamicRegistration: false
            },
            xcontentProvider: false,
            xfilesProvider: false,
        };

        this.completionKindMap = {
            1: "KW", // Text
            2: "MTH", // Method
            3: "FUN", // Function
            4: "CNST", // Constructor
            5: "ARG", // Field
            6: "VAR", // Variable
            7: "CLS", // Class
            8: "IFC", // Interface
            9: "MOD", // Module
            10: "PROP", // Property
            11: "KW", // Unit
            12: "KW", // Value
            13: "STRU", // Enum
            14: "KW", // Keyword
            15: "KW", // Snippet
            16: "KW", // Color
            17: "MOD", // File
            18: "IFC", // Reference
            19: "NMSP", // Folder
            20: "STRU", // EnumMember
            21: "CNST", // Constant
            22: "STRU", // Struct
            23: "EVNT", // Event
            24: "BI", // Operator
            25: "ARG" // TypeParameter
        };

        this.symbolKindMap = {
            1: "KW", // File
            2: "MOD", // Module
            3: "NMSP", // Namespace
            4: "MOD", // Package
            5: "CLS", // Class
            6: "MTH", // Method
            7: "PROP", // Property
            8: "IFC", // Field
            9: "CTOR", // Constructor
            10: "STRU", // Enum
            11: "IFC", // Interface
            12: "FUN", // Function
            13: "VAR", // Variable
            14: "CNST", // Constant
            15: "STR", // String
            16: "NUM", // Number
            17: "BOOL", // Boolean
            18: "ARR", // Array
            19: "OBJ", // Object
            20: "ATTR", // Key
            21: "CNST", // Null
            22: "STRU", // EnumMember
            23: "STRU", // Struct
            24: "EVNT", // Event
            25: "BI", // Operator
            26: "ARG" // TypeParameter
        };

        this.symbolKindScopes = [3,5,8,9,10,11,12,22,23];

        this.create = function (_meta)
        {
            meta = _meta;
            return this;
        };

        this.spawn = function (binary, args, env)
        {
            openDocs = {};
            diagnostics = {};
            process = child_process.spawn(binary, args, env ? {env: env} : {});
            process.stdin.write = (data) =>
            {
                data = unescape(encodeURIComponent(data));
                log.debug("Writing: " + data);
                emit(process.stdin, 'data', data);
            };

            process.stderr.on("data", reportError.bind(null, "stderr"));
            process.on("error", reportError.bind(null, "error"));
            process.on("close", (d) =>
            {
                if ( ! ready)
                {
                    this.onClosePremature();
                    failing = true;
                }

                log.debug("close: " + d);
            });

            connection = rpc.createMessageConnection(
                new rpc.StreamMessageReader(process.stdout),
                new rpc.StreamMessageWriter(process.stdin),
                {
                    error: reportError.bind(null, "connection error")
                }
            );

            connection.trace(true, { log: (data) => { log.debug(data); } });
            connection.listen();

            connection.onNotification("textDocument/publishDiagnostics", (params) =>
            {
                if ( ! params || ! params.uri)
                    return;

                diagnostics[params.uri] = {
                    version: params.version,
                    diagnostics: params.diagnostics || [],
                    timestamp: Date.now()
                };
                log.debug("publishDiagnostics: " + params.uri + " version=" + params.version +
                          " count=" + diagnostics[params.uri].diagnostics.length);
            });

            var path;
            try
            {
                path = legacy.uriparse.URIToLocalPath(legacy.places.getDirectory());
            } catch(e)
            {
                path = koShell.getCwd();
            }

            connection.sendRequest("initialize", {
                processId: appinfo.processID,
                rootPath: path,
                rootUri: legacy.uriparse.pathToURI(path),
                initializationOptions: {},
                capabilities: this.capabilities,
                trace: "verbose"
            })
            .then(() =>
            {
                connection.sendNotification(new rpc.NotificationType('initialized'), {});
                ready = true;
            })
            .catch(reportError.bind(null, "initialize caught"));
        };

        var reportError = function (description, data) 
        {
            if (data instanceof Error || (typeof data == "object" && "fileName" in data && "lineNumber" in data && "message" in data))
            {
                log.exception(data, description);
            }
            else
            {
                log.error(description + ": " + logging.getObjectTree(data), true);
            }
        };

        /**
         * Open or update the buffer in the language server.  Documents stay
         * open between requests so the server can keep its analysis state.
         */
        var openDocument = function (opts)
        {
            var args = opts.args;
            var uri = args.path ? legacy.uriparse.pathToURI(args.path) : "file://unsaved";
            var text = args.buf || "";
            var previous = openDocs[uri];
            var version = previous ? previous.version + 1 : 1;
            var sent = Date.now();

            if (previous)
            {
                var prevLines = previous.text.split(/\r\n|\r|\n/);
                connection.sendNotification(new rpc.NotificationType('textDocument/didChange'), {
                    textDocument: { uri: uri, version: version },
                    contentChanges: [{
                        range: {
                            start: { line: 0, character: 0 },
                            end: {
                                line: prevLines.length - 1,
                                character: prevLines[prevLines.length - 1].length
                            }
                        },
                        text: text
                    }]
                });
            }
            else
            {
                connection.sendNotification(new rpc.NotificationType('textDocument/didOpen'), {
                    textDocument: {
                        uri: uri,
                        languageId: (meta.opts.language || meta.language),
                        version: version,
                        text: text
                    }
                });
            }

            openDocs[uri] = { version: version, text: text };

            return { uri: uri, version: version, sent: sent };
        };

        /**
         * Wait until the server has parsed the document and published
         * diagnostics for it.  Resolves on timeout as well so a server that
         * never publishes diagnostics cannot wedge a request.
         */
        var waitForDiagnostics = function (doc, timeout)
        {
            return new Promise((resolve) =>
            {
                var deadline = Date.now() + timeout;
                var check = () =>
                {
                    var entry = diagnostics[doc.uri];
                    if (entry && entry.timestamp >= doc.sent &&
                        (entry.version === doc.version || entry.version === undefined))
                    {
                        return resolve();
                    }
                    if (Date.now() > deadline)
                    {
                        log.debug("diagnostics timeout for " + doc.uri + " version=" + doc.version);
                        return resolve();
                    }
                    w.setTimeout(check, 50);
                };
                check();
            });
        };

        /**
         * Convert the Scintilla absolute position to a zero-based LSP
         * position.
         */
        var getPosition = function (opts)
        {
            var args = opts.args;
            var before = (args.buf || "").substr(0, args.pos);
            var lines = before.split(/\r\n|\r|\n/);
            return {
                line: Math.max(lines.length - 1, 0),
                character: lines[lines.length - 1].length
            };
        };

        var uriToPath = function (uri)
        {
            try { return legacy.uriparse.URIToLocalPath(uri); }
            catch (e) { return uri; }
        };

        /**
         * Normalize definition responses.  Servers may return a Location, an
         * array of Locations or an array of LocationLinks.
         */
        var normalizeLocations = function (data)
        {
            if ( ! data)
                return [];

            if ( ! Array.isArray(data))
                data = [data];

            var locations = [];
            for (let item of data)
            {
                if ( ! item)
                    continue;

                if ("targetUri" in item)
                {
                    locations.push({
                        uri: item.targetUri,
                        range: item.targetSelectionRange || item.targetRange
                    });
                }
                else if ("uri" in item)
                {
                    locations.push({ uri: item.uri, range: item.range });
                }
            }

            return locations.filter((loc) => loc.range);
        };

        var getBufferLines = function (opts, uri)
        {
            var currentUri = opts.args.path ? legacy.uriparse.pathToURI(opts.args.path) : null;
            if (uri == currentUri || uri == "file://unsaved")
                return (opts.args.buf || "").split(/\r\n|\r|\n/);

            try { return koFile.read(uriToPath(uri)).split(/\r\n|\r|\n/); }
            catch (e) { return []; }
        };

        var getSymbolAtPosition = function (opts)
        {
            var buf = opts.args.buf || "";
            var pos = Math.min(Math.max(opts.args.pos || 0, 0), buf.length);
            var start = pos;
            var end = pos;

            while (start > 0 && /[\w$:-]/.test(buf.charAt(start - 1)))
                start--;
            while (end < buf.length && /[\w$:-]/.test(buf.charAt(end)))
                end++;

            return buf.substring(start, end);
        };

        this.onClosePremature = function () {};

        this.stop = function ()
        {
            if (connection)
            {
                for (let uri in openDocs)
                {
                    try
                    {
                        connection.sendNotification(new rpc.NotificationType('textDocument/didClose'),
                                                    { textDocument: { uri: uri } });
                    }
                    catch (e) {}
                }
            }
            if (process)
            {
                try { process.kill(); } catch (e) {}
                process = null;
            }
            connection = null;
            openDocs = {};
            diagnostics = {};
            ready = false;
        };
        
        this.isFailing = function ()
        {
            return failing === true;
        };

        this.isReady = function () 
        {
            return process && process.pid && ready;
        };

        this.supports = function (feature) 
        {
            return meta && meta.opts.supports.indexOf(feature) != -1;
        };

        this.request = function (method, args, opts) 
        {
            log.debug("request called: " + method);

            method = "mediate_" + method;

            return new koPromise((resolve, reject, each) =>
            {
                if ( ! (method in this))
                    return reject("Method not supported: " + method);

                this[method](opts, resolve, reject, each);
            });
        };


        this.getCaretScope = function (opts, resolve, reject) 
        {
            reject();
        };

        this.mediate_getSymbolsInBuffer = function (opts, resolve, reject) 
        {
            log.debug("getSymbolsInBuffer called");

            var doc = openDocument(opts);

            connection.sendRequest("textDocument/documentSymbol", {
                textDocument: { uri: doc.uri }
            })
            .then((items) =>
            {
                this.parseSymbolTree(items, opts)
                .then(resolve)
                .catch(reject);
            })
            .catch(reportError.bind(null, "getSymbolsInBuffer caught"))
            .catch(reject);
        };

        this.parseSymbolTree = function (items, opts) 
        {
            return new koPromise((resolve, reject) =>
            {
                var symbols = [];
                var containers = { global: [{ members: symbols }]};
                var defaultUri = opts.args.path ?
                    legacy.uriparse.pathToURI(opts.args.path) : "file://unsaved";
                var flatItems = [];
                var flatten = (list, containerName) =>
                {
                    for (let item of list)
                    {
                        flatItems.push([item, containerName]);
                        if (item.children && item.children.length)
                            flatten(item.children, item.name);
                    }
                };
                flatten(items, null);

                for (let [item, nestedContainer] of flatItems)
                {
                    if ( ! ("_"+item.name in containers))
                        containers["_"+item.name] = [];

                    let location = item.location || {};
                    let range = location.range || item.range;
                    if ( ! range)
                        continue;

                    let symbol = {
                        "name": item.name,
                        "typehint": null,
                        "type": this.symbolKindMap[item.kind] || "KW",
                        "filename": location.uri || defaultUri,
                        "line": range.start.line + 1,
                        "pos": -1,
                        "active": false,
                        "isScope": this.symbolKindScopes.indexOf(item.kind) != -1,
                        "source": "buffer",
                        "members": [],
                        "api": "lsp"
                    };

                    symbol = this.normalizeSymbol(symbol);

                    containers["_"+item.name].push(symbol);

                    let container = symbols;
                    let containerName = nestedContainer || item.containerName;
                    if (containerName && "_"+containerName in containers)
                    {
                        container = containers["_"+containerName].slice(-1)[0];
                        container.isScope = true;
                        container = container.members;
                    }
    
                    container.push(symbol);
                }
                
                var activeSymbol;
                var line = opts.args.line-1;
                for (let k in containers)
                {
                    for (let container of containers[k])
                    {
                        if (opts.args.sortType == "alpha")
                            container.members = container.members.sort((a,b) => a.name.localeCompare(b.name));
                        else
                            container.members = container.members.sort((a,b) => a.line - b.line);
                            
                        if (container.isScope &&
                            container.line && container.line <= line && ( ! activeSymbol || container.line > activeSymbol.line))
                        {
                            activeSymbol = container;
                        }
                    }
                }

                if (activeSymbol)
                    activeSymbol.active = true;

                resolve(symbols);
            });
        };
        
        this.normalizeSymbol = function (symbol) { return symbol; };

        this.mediate_getCaretScope = function (opts, resolve, reject)
        {
            log.debug("getCaretScope called");

            var doc = openDocument(opts);

            connection.sendRequest("textDocument/documentSymbol", {
                textDocument: { uri: doc.uri }
            })
            .then((items) =>
            {
                var activeItem;
                var activeRange;
                var line = opts.args.line-1;
                var defaultUri = opts.args.path ?
                    legacy.uriparse.pathToURI(opts.args.path) : "file://unsaved";
                var walk = (list) =>
                {
                    for (let item of list)
                    {
                        let location = item.location || {};
                        let range = location.range || item.range;
                        if (range && this.symbolKindScopes.indexOf(item.kind) != -1 &&
                            range.start.line <= line &&
                            ( ! activeRange || range.start.line > activeRange.start.line))
                        {
                            activeItem = item;
                            activeRange = range;
                        }
                        if (item.children && item.children.length)
                            walk(item.children);
                    }
                };
                walk(items);

                if ( ! activeItem)
                    return resolve();

                resolve(this.normalizeSymbol({
                    "name": activeItem.name,
                    "typehint": null,
                    "type": this.symbolKindMap[activeItem.kind] || "KW",
                    "filename": (activeItem.location || {}).uri || defaultUri,
                    "line": activeRange.start.line + 1,
                    "pos": -1,
                    "active": true,
                    "isScope": this.symbolKindScopes.indexOf(activeItem.kind) != -1,
                    "source": "buffer",
                    "members": [],
                    "api": "lsp"
                }));
            })
            .catch(reportError.bind(null, "getCaretScope caught"))
            .catch(reject);
        };

        this.mediate_getDefinition = function (opts, resolve, reject, each)
        {
            log.debug("getDefinition called");

            var doc = openDocument(opts);
            var position = getPosition(opts);

            waitForDiagnostics(doc, 2000)
            .then(() => connection.sendRequest("textDocument/definition", {
                textDocument: { uri: doc.uri },
                position: position
            }))
            .then((data) =>
            {
                log.debug("definition response: " + JSON.stringify(data));
                var locations = normalizeLocations(data);
                if ( ! locations.length)
                    return resolve();

                var location = locations[0];
                var result = {
                    filename: uriToPath(location.uri),
                    line: location.range.start.line + 1,
                    pos: location.range.start.character,
                    symbol: null
                };
                resolve(result);
            })
            .catch(reportError.bind(null, "getDefinition caught"))
            .catch(reject);
        };

        this.mediate_getReferences = function (opts, resolve, reject, each)
        {
            log.debug("getReferences called");

            var doc = openDocument(opts);
            var position = getPosition(opts);

            // The server may answer before its background analysis finishes,
            // so retry briefly when the first answer is empty.
            var requestReferences = (attempt) =>
            {
                return connection.sendRequest("textDocument/references", {
                    textDocument: { uri: doc.uri },
                    position: position,
                    context: { includeDeclaration: true }
                })
                .then((data) =>
                {
                    var locations = normalizeLocations(data);
                    log.debug("references response (attempt " + attempt + "): " +
                              locations.length);
                    if (locations.length || attempt >= 4)
                        return locations;

                    return new Promise((next) => w.setTimeout(next, 250))
                        .then(() => requestReferences(attempt + 1));
                });
            };

            waitForDiagnostics(doc, 2000)
            .then(() => requestReferences(0))
            .then((locations) =>
            {
                var name = getSymbolAtPosition(opts);
                var groups = {};

                for (let location of locations)
                {
                    if ( ! (location.uri in groups))
                        groups[location.uri] = [];
                    groups[location.uri].push(location);
                }

                for (let uri in groups)
                {
                    let lines = getBufferLines(opts, uri);
                    let path = uriToPath(uri);
                    let results = [];

                    for (let location of groups[uri])
                    {
                        let line = location.range.start.line;
                        let character = location.range.start.character;
                        let absolute = character;
                        for (let i = 0; i < line; i++)
                            absolute += (lines[i] || "").length + 1;

                        results.push({
                            name: name,
                            path: path,
                            line: line + 1,
                            pos: absolute,
                            lineData: lines[line] || ""
                        });
                    }

                    each({ "path": path, "results": results });
                }

                resolve();
            })
            .catch(reportError.bind(null, "getReferences caught"))
            .catch(reject);
        };

        this.getSignature = function (uri, position)
        {
            return connection.sendRequest("textDocument/signatureHelp", {
                textDocument: { uri: uri },
                position: position
            })
            .then((data) =>
            {
                if ( ! data || ! data.signatures || ! data.signatures.length)
                    return {};

                var signature = data.signatures[data.activeSignature || 0] || data.signatures[0];
                var docblock = signature.documentation || "";
                if (docblock && typeof docblock == "object")
                    docblock = docblock.value || "";

                return {
                    signature: signature.label || "",
                    docblock: docblock
                };
            })
            .catch(() => ({}));
        };

        this.mediate_getCompletions = function (opts, resolve, reject) 
        {
            log.debug("getCompletions called");

            var view = opts.view || views.current().get();
            var editor = koEditor.editor(view.scintilla, view.scimoz);
            var queryRx = new RegExp(`[${meta.opts.completion_query_characters || meta.opts.completion_word_characters}]`);
            var query = editor.getWord(opts.args.pos, queryRx) || "";
            query = query.toLowerCase();

            var args = opts.args;
            var viewUri = require("ko/views").current().url;
            var uri = legacy.uriparse.pathToURI(args.path);

            var pos;
            if (viewUri == uri)
                pos = require("ko/editor").getCursorPosition();
            else
            {
                var buf = args.buf.substr(args.pos).split(/\n|\r/);
                pos = {
                    line: buf.length,
                    ch: buf.slice(-1)[0].length
                };
            }

            var doc = openDocument(opts);

            connection.sendRequest("textDocument/completion", {
                textDocument: { uri: doc.uri },
                position: { line: pos.line - 1, character: pos.ch }
            })
            .then((data) =>
            {
                var items = data.items;
                items = items.filter((v) => v.label.toLowerCase().indexOf(query) != -1);

                this.parseCompletions(items, opts)
                .then((symbols) =>
                {
                    var result = {
                        "symbol": "",
                        "query": query,
                        "docblock": "",
                        "signature": "",
                        "entries": symbols,
                        "language": (meta.opts.language || meta.language)
                    };

                    // Calltips request a single completion and expect the
                    // signature/docblock fields to be populated.
                    if (opts.args.limit != 1)
                        return resolve(result);

                    this.getSignature(uri, { line: pos.line - 1, character: pos.ch })
                    .then((signature) =>
                    {
                        result.signature = signature.signature || "";
                        result.docblock = signature.docblock || "";
                        resolve(result);
                    });
                })
                .catch(reject);
            })
            .catch(reportError.bind(null, "getCompletions caught"))
            .catch(reject);
        };

        this.parseCompletions = function (items, opts) 
        {
            return new koPromise((resolve, reject) =>
            {
                var symbols = [];
                for (let item of items)
                {
                    symbols.push(this.normalizeCompletionSymbol({
                        "name": item.label,
                        "typehint": null,
                        "type": this.completionKindMap[item.kind] || "KW",
                        "filename": item.data && item.data.uri ? item.data.uri : null,
                        "line": -1,
                        "pos": item.data && item.data.offset ? item.data.offset : null,
                        "active": false,
                        "isScope": this.symbolKindScopes.indexOf(item.kind) != -1,
                        "source": "buffer",
                        "members": [],
                        "api": "lsp"
                    }));
                }
                
                resolve(symbols);
            });
        };
        
        this.normalizeCompletionSymbol = function (symbol) { return symbol; };

    }).apply(module.exports);
    
}).apply(module.exports);