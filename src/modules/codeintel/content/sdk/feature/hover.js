(function()
{

    const {Cc, Ci}  = require("chrome");
    const log = require("ko/logging").getLogger("codeintel/feature/hover");
    const codeintel = require("codeintel/codeintel");
    const service = require("codeintel/service");
    const mediator = require("codeintel/service/mediator");
    const views = require("ko/views");
    const partSvc = Cc["@activestate.com/koPartService;1"].getService(Ci.koIPartService);
    const timers = require("sdk/timers");
    const w = require("ko/windows").getMain();
    const legacy = w.ko;

    var panel = null;
    var timer = null;
    var currentView = null;

    //log.setLevel(10);

    var destroy = () =>
    {
        if (timer)
        {
            timers.clearTimeout(timer);
            timer = null;
        }
        if ( ! panel)
            return;

        try
        {
            if (panel.element.state == "open" || panel.element.state == "showing")
                panel.element.hidePopup();
        }
        catch (e) {}

        try { panel.element.removeEventListener("popuphiding", destroy); } catch (e) {}
        panel = null;
    };

    var show = (text, screenX, screenY) =>
    {
        destroy();

        panel = require("ko/ui/panel").create({ attributes:
        {
            id: "hover-panel",
            class: "dialog",
            level: "floating",
            noautohide: true,
            noautofocus: true
        } });

        var content = require("ko/ui/textbox").create({
            multiline: true,
            readonly: true,
            width: "420"
        });
        content.value(text);
        panel.addElement(content);

        panel.element.addEventListener("popuphiding", destroy, false);

        panel.open({ anchor: null, x: screenX + 12, y: screenY + 16, align: "bottom" });

        panel.element.addEventListener("popupshown", () =>
        {
            try
            {
                panel.element.sizeToContent();
                if (content.element.inputField)
                {
                    content.attr("height",
                        Math.min(content.element.inputField.scrollHeight, 220) + "px");
                    panel.element.sizeToContent();
                }
            }
            catch (e) {}
        });
    };

    var onMouseMove = (event) =>
    {
        var view = views.current().get();
        if ( ! view || ! view.scintilla || ! view.scimoz ||
             ! mediator.getMediatorByFeature(codeintel.FEATURE_HOVER, view.language))
        {
            destroy();
            return;
        }

        var target = event.target;
        var inEditor = target == view.scintilla ||
                       (view.scintilla.contains && view.scintilla.contains(target));
        if ( ! inEditor)
        {
            destroy();
            return;
        }

        // Moving the mouse invalidates the current tip; schedule a fresh one.
        destroy();

        var box = view.scintilla.boxObject;
        var pos = view.scimoz.positionFromPoint(event.screenX - box.screenX,
                                                event.screenY - box.screenY);
        if (pos < 0)
            return;

        var screenX = event.screenX;
        var screenY = event.screenY;

        timer = timers.setTimeout(() =>
        {
            timer = null;
            showForPosition(view, pos, screenX, screenY);
        }, 400);
    };

    var showForPosition = (view, pos, screenX, screenY) =>
    {
        if ( ! view || ! view.scimoz ||
             ! mediator.getMediatorByFeature(codeintel.FEATURE_HOVER, view.language))
            return;

        var curProject = partSvc.currentProject;
        var parentPath = curProject ? curProject.liveDirectory :
            legacy.uriparse.URIToPath(legacy.places.getDirectory());

        service.getHover({
            buf: view.scimoz.text,
            pos: pos,
            path: view.filePath,
            parentPath: parentPath,
            importPaths: "",
            language: view.language
        })
        .then((hover) =>
        {
            if ( ! hover || ! hover.contents)
                return;
            show(hover.contents, screenX, screenY);
        })
        .catch(() => {});
    };

    this.showForPosition = showForPosition;

    var detach = () =>
    {
        if (currentView && currentView.scintilla)
        {
            try { currentView.scintilla.removeEventListener("mousemove", onMouseMove, false); }
            catch (e) {}
        }
        currentView = null;
    };

    var attach = () =>
    {
        detach();
        var view = views.current().get();
        if ( ! view || ! view.scintilla)
            return;
        currentView = view;
        view.scintilla.addEventListener("mousemove", onMouseMove, false);
    };

    this.start = () =>
    {
        w.addEventListener("buffer_char_modified", destroy);
        w.addEventListener("current_view_changed", attach);
        w.addEventListener("deactivate", destroy);
        attach();
    };

    this.stop = () =>
    {
        w.removeEventListener("buffer_char_modified", destroy);
        w.removeEventListener("current_view_changed", attach);
        w.removeEventListener("deactivate", destroy);
        detach();
        destroy();
    };

}).apply(module.exports);
