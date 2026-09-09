package com.determinantmatrix.zhibo.core.database

import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.RoomDatabase
import androidx.room.Transaction
import kotlinx.coroutines.flow.Flow

/**
 * 关注项持久化实体。tags / fallbackPlugins 保存原始 CSV 单元格形态（| 分隔），
 * extraJson 保存与桌面 extra 列一致的 Python 风格 JSON 文本，保证导出无损。
 */
@Entity(tableName = "followers")
data class FollowerEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val position: Int,
    val name: String,
    val plugin: String,
    val url: String,
    val platform: String,
    val quality: String,
    val tags: String,
    val sportId: String,
    val extraJson: String,
    val enabled: Boolean,
    val fallbackPlugins: String,
)

@Dao
interface FollowerDao {

    @Query("SELECT * FROM followers ORDER BY position")
    fun observeAll(): Flow<List<FollowerEntity>>

    @Query("SELECT * FROM followers ORDER BY position")
    suspend fun getAll(): List<FollowerEntity>

    @Insert
    suspend fun insertAll(items: List<FollowerEntity>)

    @Query("DELETE FROM followers")
    suspend fun clear()

    @Transaction
    suspend fun replaceAll(items: List<FollowerEntity>) {
        clear()
        insertAll(items)
    }
}

@Database(entities = [FollowerEntity::class], version = 1, exportSchema = false)
abstract class ZhiboDatabase : RoomDatabase() {
    abstract fun followerDao(): FollowerDao

    companion object {
        fun build(context: android.content.Context): ZhiboDatabase =
            androidx.room.Room.databaseBuilder(
                context,
                ZhiboDatabase::class.java,
                "zhibo.db",
            ).build()
    }
}
