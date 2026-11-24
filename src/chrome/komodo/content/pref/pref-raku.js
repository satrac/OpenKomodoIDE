/* Copyright (c) 2026 ActiveState Software Inc.
   See the file LICENSE.txt for licensing information. */

var prefExecutable = null;
var programmingLanguage = "Raku";
var _bundle = Components.classes["@mozilla.org/intl/stringbundle;1"]
            .getService(Components.interfaces.nsIStringBundleService)
            .createBundle("chrome://komodo/locale/pref/pref-languages.properties");

function OnPreferencePageOK(prefset)
{
    return checkValidInterpreterSetting(prefset,
                                        "rakuDefaultInterpreter",
                                        programmingLanguage);
}

function OnPreferencePageSaved(prefset)
{
    var prefName = "rakuExtraPaths";
    var extraPaths = document.getElementById(prefName);
    var paths = extraPaths.getData();
    if (paths == "")
    {
        prefset.deletePref(prefName);
        Components.classes["@activestate.com/koPrefService;1"]
            .getService(Components.interfaces.koIPrefService).saveState();
    }
}

function PrefRaku_PopulateRakuInterps(done)
{
    var availInterpList = document.getElementById("rakuDefaultInterpreter");

    availInterpList.removeAllItems();
    availInterpList.appendItem(_bundle.formatStringFromName(
        "findingInterpreters.label", [programmingLanguage], 1));

    var appInfo = Components.classes["@activestate.com/koAppInfoEx?app=Raku;1"]
        .getService(Components.interfaces.koIAppInfoEx);
    var callback = {
        callback: function(result, availInterps)
        {
            var ok = result == Components.interfaces.koIAsyncCallback.RESULT_SUCCESSFUL;
            availInterpList.removeAllItems();
            availInterpList.appendItem(_bundle.GetStringFromName("findOnPath.label"), '');

            // The async finder can return an empty list when the interpreter
            // was configured explicitly; always offer the resolved path.
            try
            {
                var resolved = appInfo.executablePath;
                if (resolved)
                {
                    availInterps = availInterps || [];
                    if (availInterps.indexOf(resolved) == -1)
                        availInterps.push(resolved);
                }
            }
            catch (e) {}

            var found = false;
            if (!ok || !availInterps || availInterps.length == 0)
            {
                document.getElementById("no-avail-interps-message")
                    .removeAttribute("collapsed");
            }
            else
            {
                for (let interp of availInterps)
                {
                    availInterpList.appendItem(interp, interp);
                    if (interp == prefExecutable) found = true;
                }
            }
            if (!found && prefExecutable)
                availInterpList.appendItem(prefExecutable, prefExecutable);
            if (done)
                done();
        }
    };
    appInfo.FindExecutablesAsync(callback);
}

function PrefRaku_OnLoad()
{
    prefExecutable = parent.hPrefWindow.prefset.getString(
        'rakuDefaultInterpreter', '');
    PrefRaku_PopulateRakuInterps(function()
    {
        parent.hPrefWindow.onpageload();
        var extraPaths = document.getElementById("rakuExtraPaths");
        extraPaths.init(); // must happen after onpageload
        var file = getOwningFileObject();
        if (file && file.dirName) {
            extraPaths.setCwd(file.dirName);
        }
    });
}

function loadRakuExecutable()
{
    loadExecutableIntoInterpreterList("rakuDefaultInterpreter");
}
