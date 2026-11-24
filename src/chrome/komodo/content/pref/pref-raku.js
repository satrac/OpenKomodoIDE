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

function PrefRaku_PopulateRakuInterps()
{
    var availInterpList = document.getElementById("rakuDefaultInterpreter");

    availInterpList.removeAllItems();
    availInterpList.appendItem(_bundle.GetStringFromName("findOnPath.label"), '');
    if (prefExecutable)
        availInterpList.appendItem(prefExecutable, prefExecutable);
}

function PrefRaku_OnLoad()
{
    prefExecutable = parent.hPrefWindow.prefset.getString(
        'rakuDefaultInterpreter', '');
    PrefRaku_PopulateRakuInterps();

    parent.hPrefWindow.onpageload();
    var extraPaths = document.getElementById("rakuExtraPaths");
    extraPaths.init(); // must happen after onpageload
    var file = getOwningFileObject();
    if (file && file.dirName) {
        extraPaths.setCwd(file.dirName);
    }
}

function loadRakuExecutable()
{
    loadExecutableIntoInterpreterList("rakuDefaultInterpreter");
}
