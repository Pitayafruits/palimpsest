using System;
using System.IO;
using System.Drawing;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Collections.Generic;

public static class ServerSettings {
    public static string Site { get; private set; }
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
            if(String.IsNullOrWhiteSpace(value)) {
                using(var form = new Form()) {
                    form.Text="Palimpsest · 连接站点"; form.ClientSize=new Size(540,176);
                    form.StartPosition=FormStartPosition.CenterScreen; form.Font=new Font("Microsoft YaHei UI",10);
                    var caption=new Label {Text="Confluence Server / Data Center 的 HTTPS 地址：",Left=20,Top=20,Width=500};
                    var input=new TextBox {Left=20,Top=56,Width=500,Text="https://"};
                    var save=new Button {Text="连接",Left=410,Top=110,Width=110};
                    save.Click += delegate {
                        try { Site=Normalize(input.Text); form.DialogResult=DialogResult.OK; }
                        catch(Exception error) { MessageBox.Show(form,error.Message,"站点地址无效"); }
                    };
                    form.Controls.AddRange(new Control[]{caption,input,save}); form.AcceptButton=save;
                    if(form.ShowDialog()!=DialogResult.OK) return false;
                    value=Site;
                }
                Directory.CreateDirectory(DataRoot);
                File.WriteAllText(path,json.Serialize(new {base_url=Normalize(value)}),new UTF8Encoding(false));
            }
            Site=Normalize(value); Environment.SetEnvironmentVariable("PALIMPSEST_SITE",Site);
            return true;
        } catch(Exception error) { MessageBox.Show(error.Message,"无法读取站点配置"); return false; }
    }
}
