using System;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

class StartupTests {
    static void Check(bool condition,string message) { if(!condition) throw new Exception(message); }
    static string Page(string value) {
        try { return (string)typeof(SyncWindow).GetMethod("ParsePage",BindingFlags.Static|BindingFlags.NonPublic).Invoke(null,new object[]{value}); }
        catch(TargetInvocationException error) { throw error.InnerException; }
    }
    [STAThread]
    static int Main() {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        string root=Environment.GetEnvironmentVariable("PALIMPSEST_DATA");
        Check(!String.IsNullOrWhiteSpace(root),"Tests require isolated data");
        Directory.CreateDirectory(root);
        string settings=Path.Combine(root,"site.json");
        Check(ServerSettings.Configure(),"Startup must continue without a site");
        Check(ServerSettings.Site==null,"No implicit default site");
        using(var home=new SyncWindow()) {
            Check(home.Text=="Palimpsest · Markdown ↔ Confluence","Open the home page directly");
            home.Show(); Application.DoEvents(); home.Close();
        }
        foreach(string invalid in new[]{"123","http://wiki.example.com/pages/viewpage.action?pageId=123",
            "https://wiki.example.com/login?pageId=123","https://user:secret@wiki.example.com/pages/viewpage.action?pageId=123",
            "https://wiki.example.com/pages/viewpage.action?pageId=123&pageId=456"}) {
            bool rejected=false;try { Page(invalid); } catch(ArgumentException) { rejected=true; }
            Check(rejected,"Invalid links must not configure a site");
            Check(!File.Exists(settings),"Invalid link must not be saved");
        }
        var store=new CredentialStore();
        Check(Page("https://wiki.example.com:8443/wiki/pages/viewpage.action?pageId=123")=="123","Infer page ID");
        Check(ServerSettings.Site=="https://wiki.example.com:8443/wiki","Preserve context path and port");
        Check(File.Exists(settings),"Persist detected site");
        store.Save(new LoginCredentials {username="test_user",password="offline-test"});
        Environment.SetEnvironmentVariable("PALIMPSEST_SITE",null);
        Check(ServerSettings.Configure(),"Reload persisted site");
        Check(new CredentialStore().Load().username=="test_user","Credential encryption uses detected site");
        Check(Page("456")=="456","Known site supports numeric page IDs");
        bool mismatch=false;
        try { Page("https://other.example.com/pages/viewpage.action?pageId=123"); } catch(ArgumentException) { mismatch=true; }
        Check(mismatch,"Never silently switch the site used by saved credentials");
        File.WriteAllText(settings,"invalid json");
        Environment.SetEnvironmentVariable("PALIMPSEST_SITE",null);
        Check(ServerSettings.Configure() && ServerSettings.Site==null,"Damaged settings still allow home page");
        Check(ServerSettings.LoadError!=null,"Expose configuration error in the home page");
        Page("https://wiki.example.com/pages/viewpage.action?pageId=789");
        Check(ServerSettings.LoadError==null,"Valid link repairs configuration");
        Console.WriteLine("PASS: direct home, link inference, saved settings, credential isolation and recovery");
        return 0;
    }
}
