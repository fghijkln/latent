# P3：Latent 类单继承

> 状态：已实现并与 P2 模块系统一起纳入 v0.6.0。正式语义见 [SPEC.md §5](SPEC.md#5-类v03-新增)；双后端实现位于 `semant.py`、`gen_py.py`、`gen_java.py` 与 `runtime/LtRt.java`。

## 已确定的语法

```latent
class Animal:
    fn init(self, name):
        self.name = name

    fn describe(self):
        "animal:${self.name}"

class Dog(Animal):
    fn init(self, name, breed):
        super.init(self, name)
        self.breed = breed

    fn describe(self):
        super.describe(self) + " (${self.breed})"

import "models.lt" as models
class Worker(models.Base):
    fn label(self):
        super.label(self) + " (worker)"
```

`class Name:` 仍表示无父类；`class Name(Parent):` 引用同文件 Latent 类；`class Name(alias.PublicClass):` 仅用于 P2 导入别名下公开的 Latent 类。父类可前向引用；编译器收集完整类图后统一解析。父引用只接受类名或 `alias.Class`，不接受变量、表达式、Java/Python 互操作句柄或 Latent 值。

## 语义决定

- **单继承**：父类必须在编译期解析为 Latent 类；未知/非类父类、私有模块类、自继承与传递环均带源位置报错。多继承、接口及 Java class inheritance 不支持。
- **分派**：运行时按实例最具体类开始，沿显式父指针找第一个同名方法。Python 后端不借用 Python 原生 MRO，Java 后端也不使用 JVM 类继承。子类定义的方法覆盖父实现，未覆盖方法继承。
- **覆盖 arity**：普通方法覆盖必须保留与最近父实现相同的参数数量（含显式接收者），不提供 `override` 标记。设计示例同时展示 `Animal.init(self, name)` 与 `Dog.init(self, name, breed)`，这与“一律要求覆盖 arity 相同”的建议相矛盾；为允许常见的扩展构造参数，最终决定 `init` 可以改变参数数量，调用时按实际选中的实现检查，而 `super.init(self, ...)` 单独按父实现检查。
- **`super`**：仅可写作实例方法中的 `super.method(self, args...)`。第一个参数必须是当前方法显式声明的接收者。编译时绑定当前类，运行时从其**直接父类**开始找最近实现；`super` 不能作为值、不能访问字段，也不能在函数或顶层使用。
- **构造**：`Child.new(args)` 创建一个 Child 实例。若 Child 自己定义 `init`，只运行 Child 的 `init`；父构造器仅在显式调用 `super.init(self, ...)` 时执行。若 Child 没有 `init`，沿父链继承最近构造器；父类 `init` 会在子类 `new` 时自然执行一次。继承链上没有任何 `init` 时保留原有无参构造行为。
- **实例**：字段继续位于同一个动态实例存储中；真值、identity 比较与源类名打印规则不变。
- **模块**：`alias.PublicClass` 复用 P2 的文件路径解析、公开成员和符号隔离规则。模块父类可以跨文件声明，所有类元数据按继承依赖先父后子生成，避免 Python/Java 静态初始化顺序差异。

## 验收覆盖

`python3 tests/run_tests.py` 在 Python 与 Java 上逐字节对拍继承正例，包括前向声明、直接与多层继承、覆盖优先、`super` 的直接父类分派、显式与继承构造器、动态字段、对象 identity、同名类隔离和 P2 跨模块继承。负例检查未知/非类父类、私有模块父类、自继承和循环、非法 `super`、缺少父方法、接收者错误、普通方法覆盖 arity，以及继承构造器参数错误的双后端源位置。

本首版不支持类/静态方法、运算符重载或把 Java 句柄作为 Latent 父类。
