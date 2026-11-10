using System;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using System.Collections.Generic;

public class LoginCredentials {
    public string username { get; set; }
    public string password { get; set; }
}

public class CredentialStore {
    readonly string path;
    static readonly byte[] Entropy = Encoding.UTF8.GetBytes("Palimpsest|v1|" + ServerSettings.Site);
    public CredentialStore(string location = null) {
        path = location ?? Path.Combine(ServerSettings.DataRoot, "login.dat");
    }
    public void Save(LoginCredentials credentials) {
        byte[] clear = Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(credentials));
        try {
            byte[] encrypted = ProtectedData.Protect(clear,Entropy,DataProtectionScope.CurrentUser);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string temp = path + ".tmp"; File.WriteAllBytes(temp,encrypted);
            if(File.Exists(path)) File.Replace(temp,path,null); else File.Move(temp,path);
        } finally { Array.Clear(clear,0,clear.Length); }
    }
    public LoginCredentials Load() {
        if(!File.Exists(path)) return null;
        byte[] clear = ProtectedData.Unprotect(File.ReadAllBytes(path),Entropy,DataProtectionScope.CurrentUser);
        try {
            var credentials = new JavaScriptSerializer().Deserialize<LoginCredentials>(Encoding.UTF8.GetString(clear));
            if(credentials == null || String.IsNullOrWhiteSpace(credentials.username) || String.IsNullOrEmpty(credentials.password))
                throw new InvalidDataException("登录记录不完整。");
            return credentials;
        } finally { Array.Clear(clear,0,clear.Length); }
    }
    public void Delete() { if(File.Exists(path)) File.Delete(path); }
}

public static class LoginClient {
    public static LoginCredentials Verify(string username, string password) {
        ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
        var request = (HttpWebRequest)WebRequest.Create(ServerSettings.Site + "/rest/api/user/current");
        request.AllowAutoRedirect = false; request.Timeout = 25000; request.ReadWriteTimeout = 25000;
        request.Accept = "application/json";
        request.Headers[HttpRequestHeader.Authorization] = "Basic " + Convert.ToBase64String(Encoding.UTF8.GetBytes(username + ":" + password));
        try {
            using(var response = (HttpWebResponse)request.GetResponse()) {
                if(response.StatusCode != HttpStatusCode.OK) throw new InvalidOperationException("登录接口返回异常，请检查登录方式。");
                using(var reader = new StreamReader(response.GetResponseStream(),Encoding.UTF8)) {
                    return ValidateIdentity(reader.ReadToEnd(),username,password);
                }
            }
        } catch(WebException error) {
            var response = error.Response as HttpWebResponse;
            if(response != null) {
                int code = (int)response.StatusCode; string reason = response.Headers["X-Seraph-LoginReason"]; response.Dispose();
                if(reason == "AUTHENTICATION_DENIED" || reason == "AUTHENTICATED_FAILED") throw new InvalidOperationException("服务器拒绝接口认证（HTTP " + code + "，" + reason + "）。请先网页登录完成验证码，仍失败请联系管理员。");
                if(code == 401) throw new InvalidOperationException("认证失败（401），请检查用户名、密码及站点是否允许 Basic 认证。");
                if(code == 403) throw new InvalidOperationException("服务器拒绝访问登录接口（403），请检查 API 权限或访问策略。");
            }
            throw new InvalidOperationException("无法连接 Confluence，请检查网络后重试。");
        }
    }
    internal static LoginCredentials ValidateIdentity(string response, string username, string password) {
        var data = new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(response);
        if(data == null || !data.ContainsKey("type") || Convert.ToString(data["type"]) != "known"
            || !data.ContainsKey("username") || !String.Equals(Convert.ToString(data["username"]),username,StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("登录未通过，请检查账号和密码。");
        return new LoginCredentials {username=Convert.ToString(data["username"]),password=password};
    }
}
