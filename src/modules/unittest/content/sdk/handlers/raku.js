(function() {
    
    const customHandler = require("unittest/handlers/custom");
    const prefs = require("ko/prefs");
    
    var handler = require("unittest/unittest").getHandler("raku");
    
    // Run Raku test files through Perl's prove harness, which gives us TAP
    // output that the existing tap parser understands.
    var raku = prefs.getString("rakuDefaultInterpreter", "") || "raku";
    if (raku.indexOf(" ") != -1)
        raku = '"' + raku + '"';
    handler.command = "prove -v --exec " + raku +
                      " --ext .rakutest --ext .t6 -r .";
    
    this.handler = handler;
    
    this.run = customHandler.run.bind(this);
    
}).apply(module.exports);
