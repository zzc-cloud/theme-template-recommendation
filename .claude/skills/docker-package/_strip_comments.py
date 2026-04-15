import ast
import tokenize
import os
import sys


PRESERVE_PATTERNS = ('type: ignore', 'noqa', 'pragma: no cover')


def _is_docstring_node(expr_node):
    """判断 ast.Expr 节点的值是否为字符串常量（docstring 的判定条件）"""
    return (
        isinstance(expr_node.value, ast.Constant)
        and isinstance(expr_node.value.value, str)
    )


def _collect_docstring_lines(filepath):
    """使用 AST 收集 docstring 行号集合"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()

    docstring_lines = set()

    # 模块级 docstring（文件顶部的 """..."""）
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and _is_docstring_node(tree.body[0])
    ):
        docstring_lines.update(_str_linenos(tree.body[0]))

    for node in ast.walk(tree):
        if not isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and _is_docstring_node(first):
            docstring_lines.update(_str_linenos(first))

    return docstring_lines


def _str_linenos(node):
    """获取节点所在的所有行号"""
    if hasattr(node, 'lineno'):
        end_line = getattr(node, 'end_lineno', node.lineno)
        return set(range(node.lineno, end_line + 1))
    return set()


def _strip_file(filepath):
    """移除单个 .py 文件的注释和 docstring"""
    with open(filepath, 'rb') as f:
        tokens = list(tokenize.tokenize(f.readline))

    remove_lines = set()
    inline_comment_cols = {}

    # 建立行号 -> 该行是否有代码（非空白非注释 token）的映射
    line_has_code = {}
    for tok in tokens:
        lineno = tok.start[0]
        if lineno in line_has_code:
            continue
        if tok.type in frozenset((
            tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
            tokenize.DEDENT, tokenize.COMMENT, tokenize.ENCODING,
            tokenize.ENDMARKER, tokenize.ERRORTOKEN,
        )):
            continue
        line_has_code[lineno] = True

    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        if any(p in tok.string for p in PRESERVE_PATTERNS):
            continue

        lineno = tok.start[0]
        if line_has_code.get(lineno):
            # 该行有代码 → 行内注释，只 strip 掉 # 及之后
            inline_comment_cols[lineno] = tok.start[1]
        else:
            # 该行无代码（仅有空白 + 注释）→ 独立注释行，移除整行
            remove_lines.add(lineno)

    docstring_lines = _collect_docstring_lines(filepath)
    remove_lines.update(docstring_lines)

    with open(filepath, 'r', encoding='utf-8') as f:
        original_lines = f.readlines()

    result = []
    for i, line in enumerate(original_lines, 1):
        if i in remove_lines:
            continue
        if i in inline_comment_cols:
            col = inline_comment_cols[i]
            result.append(line[:col].rstrip() + '\n')
        else:
            result.append(line)

    # 合并连续空行，最多保留 2 个（PEP 8 规范）
    clean_lines = []
    blank_count = 0
    for line in result:
        if not line.strip():
            blank_count += 1
            if blank_count <= 2:
                clean_lines.append(line)
        else:
            blank_count = 0
            clean_lines.append(line)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(clean_lines)

    return len(remove_lines)


def _check_syntax(filepath):
    """语法检查，返回 (是否成功, 错误信息)"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        compile(source, filepath, 'exec')
        return True, None
    except SyntaxError as e:
        return False, f"line {e.lineno}: {e.msg}"


def main():
    target_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    target_dirs = ['src', 'scripts']

    py_files = []
    for d in target_dirs:
        dir_path = os.path.join(target_dir, d)
        if not os.path.isdir(dir_path):
            continue
        for root, _, files in os.walk(dir_path):
            for fname in files:
                if fname.endswith('.py'):
                    py_files.append(os.path.join(root, fname))

    if not py_files:
        print("No .py files found")
        return

    total_removed = 0
    syntax_errors = []

    for fp in sorted(py_files):
        rel = os.path.relpath(fp, target_dir)
        try:
            n = _strip_file(fp)
            ok, err = _check_syntax(fp)
            if ok:
                if n > 0:
                    print(f"  [OK] {rel}: removed {n} lines")
                    total_removed += n
                else:
                    print(f"  [--] {rel}: no comments")
            else:
                print(f"  [FAIL] {rel}: syntax error - {err}")
                syntax_errors.append((rel, err))
        except Exception as e:
            print(f"  [FAIL] {rel}: {e}")
            syntax_errors.append((rel, str(e)))

    print(f"\nProcessed {len(py_files)} files, removed {total_removed} lines")

    if syntax_errors:
        print(f"\n{len(syntax_errors)} file(s) failed syntax check:")
        for rel, err in syntax_errors:
            print(f"  - {rel}: {err}")
        sys.exit(1)


if __name__ == '__main__':
    main()
