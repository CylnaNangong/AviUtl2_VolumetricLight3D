#pragma once
//----------------------------------------------------------------------------------
//  スクリプトモジュール ヘッダーファイル for AviUtl ExEdit2 (Official SDK 2026/10/24, v2.1.15 ABI)
//  By ＫＥＮくん (UTF-8 + Cross-Platform Header Guard Adaptation)
//----------------------------------------------------------------------------------
#include "plugin2.h"

struct META_METHOD_FUNCTION;

enum class PARAM_TYPE : int {
    NONE          = -1,
    NIL           = 0,
    BOOLEAN       = 1,
    LIGHTUSERDATA = 2,
    NUMBER        = 3,
    STRING        = 4,
    TABLE         = 5,
    FUNCTION      = 6,
    USERDATA      = 7,
    THREAD        = 8,
};

struct SCRIPT_MODULE_PARAM {
    int (*get_param_num)();
    int (*get_param_int)(int index);
    double (*get_param_double)(int index);
    LPCSTR (*get_param_string)(int index);
    void* (*get_param_data)(int index);

    int (*get_param_table_int)(int index, LPCSTR key);
    double (*get_param_table_double)(int index, LPCSTR key);
    LPCSTR (*get_param_table_string)(int index, LPCSTR key);

    int (*get_param_array_num)(int index);
    int (*get_param_array_int)(int index, int key);
    double (*get_param_array_double)(int index, int key);
    LPCSTR (*get_param_array_string)(int index, int key);

    void (*push_result_int)(int value);
    void (*push_result_double)(double value);
    void (*push_result_string)(LPCSTR value);
    void (*push_result_data)(void* value);

    void (*push_result_table_int)(LPCSTR* key, int* value, int num);
    void (*push_result_table_double)(LPCSTR* key, double* value, int num);
    void (*push_result_table_string)(LPCSTR* key, LPCSTR* value, int num);

    void (*push_result_array_int)(int* value, int num);
    void (*push_result_array_double)(double* value, int num);
    void (*push_result_array_string)(LPCSTR* value, int num);

    void (*set_error)(LPCSTR message);

    bool (*get_param_boolean)(int index);
    void (*push_result_boolean)(bool value);
    bool (*get_param_table_boolean)(int index, LPCSTR key);
    void (*push_result_array_boolean)(bool* value, int num);
    void (*push_result_table_boolean)(LPCSTR* key, bool* value, int num);

    // Added in AviUtl ExEdit2 beta46 (2004600); string-returning items stabilized in v2.0.54 (2005400):
    // Lock-free read-only EDIT_SECTION* bound to the active script rendering context!
    EDIT_SECTION* edit;

    void (*push_result_function)(void (*func)(SCRIPT_MODULE_PARAM*), void* userdata);
    void (*deprecated_push_result_meta_table)(void (*func_getter)(SCRIPT_MODULE_PARAM*), void (*func_setter)(SCRIPT_MODULE_PARAM*), void* userdata);
    void* userdata;
    void (*push_result_meta_table)(META_METHOD_FUNCTION* meta_method_functions, void* userdata);
    void* (*get_param_meta_table)(int index, META_METHOD_FUNCTION* meta_method_functions);
    PARAM_TYPE (*get_param_type)(int index);
};

struct META_METHOD_FUNCTION {
    LPCSTR method;
    void (*func)(SCRIPT_MODULE_PARAM*);
};

struct SCRIPT_MODULE_FUNCTION {
    LPCWSTR name;
    void (*func)(SCRIPT_MODULE_PARAM*);
};

struct SCRIPT_MODULE_TABLE {
    LPCWSTR information;
    SCRIPT_MODULE_FUNCTION* functions;
};
