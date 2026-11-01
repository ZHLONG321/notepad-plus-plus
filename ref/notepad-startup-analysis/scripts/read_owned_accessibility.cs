// Read an explicitly owned window's accessibility tree without sending input.
using System;
using System.Windows.Automation;
using System.Xml.Linq;

class ReadOwnedAccessibility
{
    static XElement Read(AutomationElement element, int depth, ref int remaining)
    {
        if (--remaining < 0) throw new InvalidOperationException("Accessibility tree exceeded the bounded observation.");
        var data = element.Current;
        var node = new XElement("control", new XAttribute("type", data.ControlType.ProgrammaticName),
            new XAttribute("name", data.Name ?? ""), new XAttribute("id", data.AutomationId ?? ""),
            new XAttribute("class", data.ClassName ?? ""));
        object pattern;
        if (element.TryGetCurrentPattern(ValuePattern.Pattern, out pattern))
            node.SetAttributeValue("value", ((ValuePattern)pattern).Current.Value);
        if (element.TryGetCurrentPattern(GridPattern.Pattern, out pattern))
        {
            var grid = (GridPattern)pattern;
            node.SetAttributeValue("rows", grid.Current.RowCount);
            node.SetAttributeValue("columns", grid.Current.ColumnCount);
        }
        if (depth > 0)
        {
            var walker = TreeWalker.ControlViewWalker;
            for (var child = walker.GetFirstChild(element); child != null; child = walker.GetNextSibling(child))
                node.Add(Read(child, depth - 1, ref remaining));
        }
        return node;
    }

    [MTAThread]
    static int Main(string[] args)
    {
        try
        {
            var root = AutomationElement.FromHandle(new IntPtr(long.Parse(args[0])));
            if (root.Current.ProcessId != int.Parse(args[1])) throw new InvalidOperationException("Window belongs to another process.");
            int remaining = 200;
            Console.OutputEncoding = new System.Text.UTF8Encoding(false);
            Console.WriteLine(Read(root, 8, ref remaining));
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
