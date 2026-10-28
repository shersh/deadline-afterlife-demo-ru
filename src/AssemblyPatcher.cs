using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using Mono.Cecil;
using Mono.Cecil.Cil;

public sealed class Translation
{
    public string type { get; set; }
    public string method { get; set; }
    public string source_hash { get; set; }
    public string target { get; set; }
}

public static class AssemblyPatcher
{
    private static IEnumerable<TypeDefinition> AllTypes(IEnumerable<TypeDefinition> types)
    {
        foreach (var type in types)
        {
            yield return type;
            foreach (var child in AllTypes(type.NestedTypes)) yield return child;
        }
    }

    private static string Hash(string value)
    {
        using (var sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", "").ToLowerInvariant();
    }

    private static string Key(string type, string method, string hash)
    {
        return type + "\n" + method + "\n" + hash;
    }

    public static int Main(string[] args)
    {
        try
        {
            if (args.Length != 4) throw new ArgumentException("Usage: AssemblyPatcher original.dll output.dll translations.json game-managed-folder");
            var resolver = new DefaultAssemblyResolver();
            resolver.AddSearchDirectory(Path.GetDirectoryName(Path.GetFullPath(args[0])));
            resolver.AddSearchDirectory(Path.GetFullPath(args[3]));
            using (var assembly = AssemblyDefinition.ReadAssembly(args[0], new ReaderParameters { AssemblyResolver = resolver, InMemory = true }))
            {
                var rows = new JavaScriptSerializer().Deserialize<List<Translation>>(File.ReadAllText(args[2], Encoding.UTF8));
                var translations = rows.ToDictionary(r => Key(r.type, r.method, r.source_hash));
                var hits = rows.ToDictionary(r => Key(r.type, r.method, r.source_hash), r => 0);
                foreach (var type in AllTypes(assembly.MainModule.Types))
                foreach (var method in type.Methods.Where(m => m.HasBody))
                foreach (var instruction in method.Body.Instructions.Where(i => i.OpCode == OpCodes.Ldstr))
                {
                    var key = Key(type.FullName, method.Name, Hash((string)instruction.Operand));
                    Translation row;
                    if (translations.TryGetValue(key, out row))
                    {
                        instruction.Operand = row.target;
                        hits[key]++;
                    }
                }
                if (hits.Values.Any(n => n == 0)) throw new InvalidDataException("One or more translation sites do not match this game build.");
                int renderCalls = AddDisplayTranslation(assembly.MainModule);
                assembly.Write(args[1]);
                using (var verification = AssemblyDefinition.ReadAssembly(args[1]))
                {
                    if (!verification.MainModule.Types.Any(t => t.FullName == "Deadline.RussianDisplay"))
                        throw new InvalidDataException("Assembly verification failed.");
                }
                Console.WriteLine("Assembly: " + hits.Values.Sum() + " translated strings, " + renderCalls + " display boundaries.");
            }
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
            return 1;
        }
    }

    private static int AddDisplayTranslation(ModuleDefinition module)
    {
        var displayType = new TypeDefinition("Deadline", "RussianDisplay", TypeAttributes.Public | TypeAttributes.Abstract | TypeAttributes.Sealed, module.TypeSystem.Object);
        module.Types.Add(displayType);
        var display = new MethodDefinition("Text", MethodAttributes.Public | MethodAttributes.Static, module.TypeSystem.String);
        display.Parameters.Add(new ParameterDefinition("value", ParameterAttributes.None, module.TypeSystem.String));
        displayType.Methods.Add(display);
        var replace = module.GetMemberReferences().OfType<MethodReference>().First(r => r.DeclaringType.FullName == "System.String" && r.Name == "Replace" && r.Parameters.Count == 2 && r.Parameters.All(p => p.ParameterType.FullName == "System.String"));
        var il = display.Body.GetILProcessor();
        var valid = Instruction.Create(OpCodes.Ldarg_0);
        il.Append(Instruction.Create(OpCodes.Ldarg_0));
        il.Append(Instruction.Create(OpCodes.Brtrue, valid));
        il.Append(Instruction.Create(OpCodes.Ldnull));
        il.Append(Instruction.Create(OpCodes.Ret));
        il.Append(valid);
        string[][] labels = { new[] { "ASCEND", "ВВЕРХ" }, new[] { "DESCEND", "ВНИЗ" } };
        foreach (var pair in labels)
        {
            il.Append(Instruction.Create(OpCodes.Ldstr, pair[0]));
            il.Append(Instruction.Create(OpCodes.Ldstr, pair[1]));
            il.Append(Instruction.Create(OpCodes.Callvirt, replace));
        }
        il.Append(Instruction.Create(OpCodes.Ret));
        int count = 0;
        foreach (var type in AllTypes(module.Types).ToArray())
        foreach (var method in type.Methods.Where(m => m.HasBody && m != display))
        {
            bool changed = false;
            foreach (var instruction in method.Body.Instructions.ToArray())
            {
                var setter = instruction.Operand as MethodReference;
                if ((instruction.OpCode != OpCodes.Call && instruction.OpCode != OpCodes.Callvirt) || setter == null || setter.Name != "set_text" || setter.Parameters.Count != 1 || setter.Parameters[0].ParameterType.FullName != "System.String") continue;
                if (setter.DeclaringType.Namespace != "TMPro" && setter.DeclaringType.FullName != "UnityEngine.TextMesh" && setter.DeclaringType.FullName != "UnityEngine.UI.Text") continue;
                var original = Instruction.Create(instruction.OpCode, setter);
                instruction.OpCode = OpCodes.Call;
                instruction.Operand = display;
                method.Body.GetILProcessor().InsertAfter(instruction, original);
                count++;
                changed = true;
            }
            if (changed)
            {
                var shorts = new[] { OpCodes.Br_S, OpCodes.Brfalse_S, OpCodes.Brtrue_S, OpCodes.Beq_S, OpCodes.Bge_S, OpCodes.Bge_Un_S, OpCodes.Bgt_S, OpCodes.Bgt_Un_S, OpCodes.Ble_S, OpCodes.Ble_Un_S, OpCodes.Blt_S, OpCodes.Blt_Un_S, OpCodes.Bne_Un_S, OpCodes.Leave_S };
                var longs = new[] { OpCodes.Br, OpCodes.Brfalse, OpCodes.Brtrue, OpCodes.Beq, OpCodes.Bge, OpCodes.Bge_Un, OpCodes.Bgt, OpCodes.Bgt_Un, OpCodes.Ble, OpCodes.Ble_Un, OpCodes.Blt, OpCodes.Blt_Un, OpCodes.Bne_Un, OpCodes.Leave };
                foreach (var instruction in method.Body.Instructions)
                {
                    int index = Array.IndexOf(shorts, instruction.OpCode);
                    if (index >= 0) instruction.OpCode = longs[index];
                }
            }
        }
        return count;
    }
}
