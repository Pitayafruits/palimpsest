using System;
using System.IO;
using System.Drawing;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Collections.Generic;
using System.Text.RegularExpressions;

public static class ServerSettings {
    public static string Site { get; private set; }
    public static string LoadError { get; private set; }
    public static string DataRoot { get {
        return Environment.GetEnvironmentVariable("PALIMPSEST_DATA") ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Palimpsest");
    } }
    public static string Normalize(string value) {
        Uri uri;
        if(!Uri.TryCreate(value.Trim(),UriKind.Absolute,out uri) || uri.Scheme != "https" ||
           uri.UserInfo.Length != 0 || uri.Query.Length != 0 || uri.Fragment.Length != 0 || value.Contains("\\"))
            throw new ArgumentException("请输入 HTTPS 站点地址，例如 https://wiki.example.com/wiki。");
        return uri.GetLeftPart(UriPartial.Authority) + uri.AbsolutePath.TrimEnd('/');
    }
    public static bool Configure() {
        var json = new JavaScriptSerializer();
        var path = Path.Combine(DataRoot,"site.json");
        try {
            string value = Environment.GetEnvironmentVariable("PALIMPSEST_SITE");
            if(String.IsNullOrWhiteSpace(value) && File.Exists(path))
                value = json.Deserialize<Dictionary<string,string>>(File.ReadAllText(path,Encoding.UTF8))["base_url"];
            Site=String.IsNullOrWhiteSpace(value) ? null : Normalize(value);
            LoadError=null;
            Environment.SetEnvironmentVariable("PALIMPSEST_SITE",Site);
            return true;
        } catch(Exception) {
            Site=null; LoadError="无法读取站点配置，请在首页粘贴完整的 Confluence 文档链接。";
            Environment.SetEnvironmentVariable("PALIMPSEST_SITE",null);
            return true;
        }
    }
    public static void EnsureFromPageLink(string value) {
        if(!String.IsNullOrWhiteSpace(Site)) return;
        Uri url;
        const string suffix="/pages/viewpage.action";
        if(!Uri.TryCreate(value.Trim(),UriKind.Absolute,out url) || url.Scheme!="https" ||
           url.UserInfo.Length!=0 || value.Contains("\\") || !url.AbsolutePath.EndsWith(suffix,StringComparison.Ordinal) ||
           Regex.Matches(url.Query,@"(?:\?|&)pageId=").Count!=1 ||
           !Regex.IsMatch(url.Query,@"(?:\?|&)pageId=[1-9][0-9]*(?:&|$)"))
            throw new ArgumentException("请先粘贴完整的 Confluence 文档链接，程序会自动识别站点。");
        string detected=Normalize(url.GetLeftPart(UriPartial.Authority)+url.AbsolutePath.Substring(0,url.AbsolutePath.Length-suffix.Length));
        Directory.CreateDirectory(DataRoot);
        string path=Path.Combine(DataRoot,"site.json"),temporary=path+".tmp";
        File.WriteAllText(temporary,new JavaScriptSerializer().Serialize(new {base_url=detected}),new UTF8Encoding(false));
        if(File.Exists(path)) File.Replace(temporary,path,null); else File.Move(temporary,path);
        Site=detected; LoadError=null;
        Environment.SetEnvironmentVariable("PALIMPSEST_SITE",Site);
    }
}
