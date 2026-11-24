/* Copyright (c) 2026 ActiveState Software Inc.
   See the file LICENSE.txt for licensing information. */

var programmingLanguage = "Lua";

function OnPreferencePageOK(prefset)
{
    return checkValidInterpreterSetting(prefset,
                                        "luaDefaultInterpreter",
                                        programmingLanguage,
                                        "luaItem");
}

function PrefLua_OnLoad()
{
    parent.hPrefWindow.onpageload();
}

function loadLuaExecutable()
{
    loadExecutableIntoTextField("luaDefaultInterpreter");
}
